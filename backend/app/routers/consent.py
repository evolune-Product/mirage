import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import current_account
from ..db import Account, Replica, get_session
from ..models_billing import AuditLog, ConsentChallenge, ConsentRecord
from .. import consent_verify, facematch, settings, storage, voiceprint
from ..models_safety import ConsentVerification
from ..models_sec import FaceBinding
from ..safety import (CODE_WORDS, audit, code_words_present, enforce_rate_limit, has_consent, moderate,
                      phrase_score)

log = logging.getLogger("mirage.consent")
PHRASE_MIN_SCORE = 0.8

router = APIRouter()


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", "", t.lower()).split())


def _own_replica(s: Session, rid: str, acc: Account) -> Replica:
    r = s.get(Replica, rid)
    if not r or r.account_id != acc.id:
        raise HTTPException(404, "replica not found")
    return r


@router.post("/replicas/{rid}/consent/challenge")
def challenge(rid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    r = _own_replica(s, rid, acc)
    code = ", ".join(secrets.choice(CODE_WORDS) for _ in range(3))
    phrase = (f"I consent to Mirage creating an AI replica of my face and voice named {r.name}. "
              f"My verification code is {code}.")
    ch = ConsentChallenge(replica_id=rid, account_id=acc.id, phrase=phrase,
                          expires_at=datetime.now(timezone.utc) + timedelta(minutes=15))
    s.add(ch); s.commit(); s.refresh(ch)
    return {"challenge_id": ch.id, "phrase": phrase, "expires_at": ch.expires_at}


class ConsentIn(BaseModel):
    challenge_id: str
    speaker_name: str
    audio_url: str
    transcript: str  # transcription of the recorded audio (client- or worker-produced)


@router.post("/replicas/{rid}/consent")
def record_consent(rid: str, body: ConsentIn, acc: Account = Depends(current_account),
                   s: Session = Depends(get_session)):
    enforce_rate_limit(acc.api_key, 20, 60, "consent")
    if not settings.allow_typed_consent():
        raise HTTPException(403, {"error": "typed_consent_disabled",
                                  "message": "record your voice and POST it to /consent/audio instead"})
    _own_replica(s, rid, acc)
    ch = s.get(ConsentChallenge, body.challenge_id)
    if not ch or ch.replica_id != rid or ch.account_id != acc.id:
        raise HTTPException(404, "challenge not found")
    exp = ch.expires_at if ch.expires_at.tzinfo else ch.expires_at.replace(tzinfo=timezone.utc)
    if ch.used or exp < datetime.now(timezone.utc):
        raise HTTPException(410, "challenge expired or already used")
    if _norm(body.transcript) != _norm(ch.phrase):
        audit(s, acc.id, "consent.rejected", rid, "transcript mismatch")
        raise HTTPException(422, "spoken phrase does not match the challenge phrase")
    ch.used = True
    rec = ConsentRecord(replica_id=rid, account_id=acc.id, speaker_name=body.speaker_name, phrase=ch.phrase,
                        transcript=body.transcript, audio_url=body.audio_url,
                        audio_sha256=hashlib.sha256(body.audio_url.encode()).hexdigest(),
                        verified_by="typed-transcript (dev only)")
    s.add_all([ch, rec]); s.commit(); s.refresh(rec)
    audit(s, acc.id, "consent.recorded", rid, rec.id)
    s.refresh(rec)  # audit() commits, which expires rec
    return rec


def _load_challenge(s: Session, rid: str, acc: Account, cid: str) -> ConsentChallenge:
    ch = s.get(ConsentChallenge, cid)
    if not ch or ch.replica_id != rid or ch.account_id != acc.id:
        raise HTTPException(404, "challenge not found")
    exp = ch.expires_at if ch.expires_at.tzinfo else ch.expires_at.replace(tzinfo=timezone.utc)
    if ch.used or exp < datetime.now(timezone.utc):
        raise HTTPException(410, "challenge expired or already used")
    return ch


@router.post("/replicas/{rid}/consent/audio")
async def record_consent_audio(rid: str, challenge_id: str = Form(...), speaker_name: str = Form(...),
                               file: UploadFile = File(...), acc: Account = Depends(current_account),
                               s: Session = Depends(get_session)):
    """Spoken consent: the server transcribes the recording, checks the challenge phrase + unique code words,
    stores the audio with its sha256 and compares the speaker's voice with the training video."""
    enforce_rate_limit(acc.api_key, 10, 60, "consent_audio")
    rep = _own_replica(s, rid, acc)
    ch = _load_challenge(s, rid, acc, challenge_id)
    limit = settings.max_upload_bytes()
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "audio too large")
    if len(data) < 2000:
        raise HTTPException(422, "audio is empty or too short")
    rec = ConsentRecord(replica_id=rid, account_id=acc.id, speaker_name=speaker_name.strip()[:200],
                        phrase=ch.phrase, transcript="", audio_url="", verified_by="pending")
    ext = Path(file.filename or "").suffix.lower()
    ext = ext if ext in (".webm", ".wav", ".ogg", ".mp3", ".m4a", ".mp4") else ".webm"
    path = consent_verify.consent_dir(rid) / f"{rec.id}{ext}"
    path.write_bytes(data)
    try:
        import asyncio
        res = await asyncio.to_thread(_verify, rep, ch, path)
    except HTTPException:
        path.unlink(missing_ok=True)
        audit(s, acc.id, "consent.rejected", rid, "audio verification failed")
        raise
    except Exception as e:
        path.unlink(missing_ok=True)
        log.warning("consent audio failure: %s", settings.redact(str(e)))
        raise HTTPException(422, "could not process the recording") from e
    transcript, pscore, voice_score, voice_status = res
    try:  # face-to-consent binding on the SAME recording (selfie video while reading the phrase)
        fres = await asyncio.to_thread(_face, rep, path)
    except HTTPException:
        path.unlink(missing_ok=True)
        audit(s, acc.id, "consent.rejected", rid, "face verification failed")
        raise
    except Exception as e:  # noqa: BLE001
        path.unlink(missing_ok=True)
        log.warning("consent face failure: %s", settings.redact(str(e)))
        raise HTTPException(422, "could not process the video in the recording") from e
    rec_sha = consent_verify.sha256_file(path)  # hash of the recording as received
    if facematch.has_video(path):  # the face video is NOT kept: evidence = audio-only copy + scores + hash
        try:
            apath = facematch.strip_video(path, path.parent)
        except Exception:  # noqa: BLE001
            path.unlink(missing_ok=True)
            raise HTTPException(422, "could not process the recording")
        path.unlink(missing_ok=True)
        path = apath
    sha = consent_verify.sha256_file(path)
    storage.publish(path)  # durable copy of the evidence (no-op on local storage)
    ch.used = True
    rec.transcript, rec.audio_sha256 = transcript, sha
    rec.audio_url = f"consent://{rid}/{rec.id}"
    rec.verified_by = "asr-phrase+voice-match" if voice_status == "match" else f"asr-phrase (voice:{voice_status})"
    s.add_all([ch, rec, ConsentVerification(
        consent_id=rec.id, replica_id=rid, audio_path=str(path), audio_sha256=sha, transcript=transcript,
        phrase_score=round(pscore, 3), code_words_ok=True, voice_score=voice_score, voice_status=voice_status),
        FaceBinding(consent_id=rec.id, replica_id=rid, recording_sha256=rec_sha, ref_kind=fres.ref_kind,
                    face_status=fres.face_status, face_score=fres.face_score,
                    threshold=facematch.threshold(), frames_used=fres.frames_used, live_status=fres.live_status,
                    live_nonrigid=fres.live.get("nonrigid"), live_mouth=fres.live.get("mouth_motion"),
                    live_texture=fres.live.get("texture_motion"), live_residual=fres.live.get("mouth_residual"), live_reasons="; ".join(fres.live_reasons))])
    s.commit(); s.refresh(rec)
    audit(s, acc.id, "consent.recorded", rid,
          f"{rec.id} voice={voice_status} score={voice_score} face={fres.face_status} score={fres.face_score} live={fres.live_status}")
    s.refresh(rec)
    out = rec.model_dump(mode="json")
    out.update(phrase_score=round(pscore, 3), voice_score=voice_score, voice_status=voice_status,
               face_status=fres.face_status, face_score=fres.face_score, liveness=fres.live_status,
               liveness_reasons=fres.live_reasons)
    return out


def _verify(rep: Replica, ch: ConsentChallenge, path: Path):
    """Blocking work (ffmpeg, whisper, onnx). -> (transcript, phrase_score, voice_score, voice_status)."""
    try:
        wav = voiceprint.decode_audio(path, 90)
    except Exception:
        raise HTTPException(422, "could not decode the recording (use a normal audio file)")
    if len(wav) < 16000 * 2:
        raise HTTPException(422, "recording is too short; read the whole phrase")
    transcript = consent_verify.transcribe(wav)
    pscore = phrase_score(ch.phrase, transcript)
    if pscore < PHRASE_MIN_SCORE or not code_words_present(ch.phrase, transcript):
        raise HTTPException(422, {"error": "phrase_mismatch",
                                  "message": "the recording does not match the challenge phrase and code words",
                                  "heard": transcript, "score": round(pscore, 2)})
    from .. import creative_jobs

    if creative_jobs.is_photo_replica(rep.id):  # photo replica: there is no voice in the training material to compare against
        return transcript, pscore, None, "skipped"
    mode = settings.voice_match_mode()
    if mode == "off":
        return transcript, pscore, None, "skipped"
    try:
        spk = voiceprint.embed(wav)
        ref = consent_verify.train_voiceprint(rep.id, rep.train_video_url)
        score = round(voiceprint.similarity(spk, ref), 3)
        status = "match" if score >= settings.voice_match_threshold() else "mismatch"
    except voiceprint.VoiceModelUnavailable:
        score, status = None, "unavailable"
    except ValueError as e:
        score, status = None, "no_speech"
        log.info("voice match impossible: %s", e)
    except Exception as e:  # download failure etc.
        score, status = None, "unavailable"
        log.warning("voice match error: %s", settings.redact(str(e)))
    if mode == "enforce" and status != "match":
        detail = {"mismatch": "the voice in the recording does not match the voice in the training video",
                  "no_speech": "not enough clear speech in the recording or the training video",
                  "unavailable": "voice verification is unavailable right now; try again later"}[status]
        raise HTTPException(503 if status == "unavailable" else 422,
                            {"error": f"voice_{status}", "message": detail, "voice_score": score})
    return transcript, pscore, score, status


def _face(rep: Replica, path: Path) -> "facematch.FaceResult":
    """Blocking: bind the face in the recording to the replica's source face + passive liveness. Raises HTTPException
    in enforce mode when the binding fails; in warn mode the statuses are only recorded."""
    from .. import creative_jobs

    fmode, lmode = facematch.mode(), facematch.liveness_mode()
    photo = creative_jobs.is_photo_replica(rep.id)
    kind = "photo" if photo else "training video"
    if fmode == "off" and lmode == "off":
        return facematch.FaceResult(ref_kind="photo" if photo else "video")
    try:
        r = facematch.verify(rep.id, rep.train_video_url, photo, path)
    except facematch.FaceUnavailable:
        r = facematch.FaceResult(face_status="unavailable", ref_kind="photo" if photo else "video")
    except ValueError:  # the clip's video could not be decoded
        r = facematch.FaceResult(face_status="no_face", ref_kind="photo" if photo else "video")
    if fmode == "enforce" and r.face_status != "match":
        msg = {"no_video": "the consent recording must be a video that shows your face while you read the phrase",
               "no_face": "we could not see one clear face for long enough; face the camera in good light, alone in frame",
               "mismatch": f"the face in the recording does not match the face in the {kind}",
               "no_reference": f"no clear face was found in the {kind}",
               "unavailable": "face verification is unavailable right now; try again later",
               "skipped": "face verification did not run"}[r.face_status]
        raise HTTPException(503 if r.face_status == "unavailable" else 422,
                            {"error": f"face_{r.face_status}", "message": msg, "face_score": r.face_score})
    if lmode == "enforce" and r.live_status == "fail":
        raise HTTPException(422, {"error": "liveness_failed", "reasons": r.live_reasons,
                                  "message": "we could not confirm a live person: " + "; ".join(r.live_reasons)})
    return r


@router.get("/replicas/{rid}/consent/{consent_id}/audio")
def consent_audio_file(rid: str, consent_id: str, acc: Account = Depends(current_account),
                       s: Session = Depends(get_session)):
    """Owner-only download of the stored consent recording (evidence)."""
    _own_replica(s, rid, acc)
    v = s.exec(select(ConsentVerification).where(ConsentVerification.consent_id == consent_id,
                                                 ConsentVerification.replica_id == rid)).first()
    if not v or not (storage.ensure_local(Path(v.audio_path)) if Path(v.audio_path).is_absolute() else Path(v.audio_path).exists()):
        raise HTTPException(404, "not found")
    return FileResponse(v.audio_path, headers={"Cache-Control": "no-store"})


@router.get("/replicas/{rid}/consent/{consent_id}/verification")
def consent_verification(rid: str, consent_id: str, acc: Account = Depends(current_account),
                         s: Session = Depends(get_session)):
    _own_replica(s, rid, acc)
    v = s.exec(select(ConsentVerification).where(ConsentVerification.consent_id == consent_id,
                                                 ConsentVerification.replica_id == rid)).first()
    if not v:
        raise HTTPException(404, "not found")
    fb = s.exec(select(FaceBinding).where(FaceBinding.consent_id == consent_id, FaceBinding.replica_id == rid)).first()
    return {**v.model_dump(mode="json"), "face": fb.model_dump(mode="json") if fb else None}


@router.get("/replicas/{rid}/consent")
def get_consent(rid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _own_replica(s, rid, acc)
    recs = s.exec(select(ConsentRecord).where(ConsentRecord.replica_id == rid)).all()
    return {"replica_id": rid, "has_consent": has_consent(s, rid), "records": recs}


@router.delete("/replicas/{rid}/consent")
def revoke_consent(rid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _own_replica(s, rid, acc)
    n = 0
    for rec in s.exec(select(ConsentRecord).where(ConsentRecord.replica_id == rid)).all():
        rec.revoked = True; s.add(rec); n += 1
    s.commit()
    for f in consent_verify.consent_dir(rid).glob("ref_face_*"):  # biometric reference templates go with the consent
        f.unlink(missing_ok=True)
    audit(s, acc.id, "consent.revoked", rid, f"{n} records")
    from ..voice_clone.service import delete_voice

    delete_voice(s, rid, acc.id, reason="revoked")  # a cloned voice must not outlive its consent
    return {"revoked": n}


class ModerateIn(BaseModel):
    text: str


@router.post("/moderation/check")
def moderation_check(body: ModerateIn, acc: Account = Depends(current_account)):
    r = moderate(body.text)
    return {"allowed": r.allowed, "reasons": r.reasons, "classifier": r.classifier}


@router.get("/audit")
def audit_log(limit: int = 100, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    return s.exec(select(AuditLog).where(AuditLog.account_id == acc.id)
                  .order_by(AuditLog.created_at.desc()).limit(min(limit, 500))).all()
