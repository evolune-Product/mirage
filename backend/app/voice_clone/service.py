"""Voice cloning business logic: consent gate, reference extraction, registration, quality check, deletion.

CONSENT RULES (enforced here and re-checked at every synthesis, so revoking consent stops a clone immediately):
  * a cloned voice exists only for a replica that has a non-revoked ConsentRecord whose voice was verified against the
    training video (`verified_by == "asr-phrase+voice-match"`); in dev (non-production) the typed-consent record is also
    accepted so the test-suite and local demos work, exactly like the rest of the consent flow;
  * the voice belongs to the replica's account; other accounts cannot see or use it;
  * the reference clip and everything derived from it live in `<replica_dir>/voice_clone/` and are removed when the
    replica is deleted (data_deletion), the account is deleted, the consent is revoked, or DELETE /replicas/{id}/voice;
  * every lifecycle step writes an AuditLog row (voice.clone_requested / ready / failed / used / fallback / deleted / revoked).
"""
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from sqlmodel import Session, SQLModel, select

from .. import db, jobs, settings
from ..models_billing import ConsentRecord
from ..models_voice import ReplicaVoice
from ..safety import audit
from . import refprep
from .metrics import wer

log = logging.getLogger("mirage.voice_clone")

ENGINE = "chatterbox-mlx"
TEST_SENTENCE = "Hello, this is a quick check of my cloned voice. The weather today is lovely, and I am happy to help."
MIN_SIMILARITY = float(os.environ.get("MIRAGE_CLONE_MIN_SIMILARITY", "0.30"))  # below this the clone is rejected
VOICE_PREFIX = "clone"


class CloneRefused(Exception):
    """Cloning not allowed (maps to HTTP 403/409 in the API)."""

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- voice ids
def voice_id(rid: str) -> str:
    return f"{VOICE_PREFIX}:{rid}"


def parse_voice(voice: str, default_rid: str = "") -> tuple[str, str] | None:
    """'clone', 'clone:<rid>', 'clone:<rid>@es' -> (rid, language). None if it is not a clone voice."""
    v = (voice or "")
    if v != VOICE_PREFIX and not v.startswith(VOICE_PREFIX + ":"):
        return None
    body = v[len(VOICE_PREFIX) + 1:]
    rid, _, lang = body.partition("@")
    return (rid or default_rid), (lang or "en")


# ---------------------------------------------------------------- paths / switches
def voice_dir(rid: str) -> Path:
    return jobs.replica_dir(rid) / "voice_clone"


def ref_path(rid: str) -> Path:
    return voice_dir(rid) / "reference.wav"


def engine_installed() -> bool:
    from .sidecar import sidecar_python

    return sidecar_python() is not None


def enabled() -> bool:
    return os.environ.get("MIRAGE_VOICE_CLONE", "1") != "0"


def ensure_table() -> None:
    SQLModel.metadata.create_all(db.engine, tables=[ReplicaVoice.__table__])


# ---------------------------------------------------------------- consent gate
def _verified(rec: ConsentRecord) -> bool:
    vb = rec.verified_by or ""
    if vb == "asr-phrase+voice-match":
        return True
    # dev only: typed consent (the whole typed path is disabled in production by settings.allow_typed_consent)
    return vb.startswith("typed-transcript") and not settings.is_production() and settings.allow_typed_consent()


def active_consent(s: Session, rid: str) -> ConsentRecord | None:
    recs = s.exec(select(ConsentRecord).where(ConsentRecord.replica_id == rid, ConsentRecord.revoked == False)  # noqa: E712
                  .order_by(ConsentRecord.created_at.desc())).all()
    for r in recs:
        if _verified(r):
            return r
    return None


def require_consent(s: Session, rid: str) -> ConsentRecord:
    rec = active_consent(s, rid)
    if rec is None:
        anyrec = s.exec(select(ConsentRecord).where(ConsentRecord.replica_id == rid, ConsentRecord.revoked == False)).first()  # noqa: E712
        raise CloneRefused(
            "voice cloning needs an active, voice-verified consent record for this replica"
            + (" (the consent on file was not matched against the speaker's voice)" if anyrec else
               " (none on file or it was revoked)"))
    return rec


# ---------------------------------------------------------------- status
def get_voice(s: Session, rid: str) -> ReplicaVoice | None:
    ensure_table()
    return s.get(ReplicaVoice, rid)


def to_dict(v: ReplicaVoice | None, rid: str) -> dict:
    if v is None:
        return {"replica_id": rid, "voice_id": voice_id(rid), "status": "none", "cloned": True}
    q = {}
    try:
        q = json.loads(v.ref_quality) if v.ref_quality else {}
    except ValueError:
        pass
    return {"replica_id": rid, "voice_id": voice_id(rid), "status": v.status, "engine": v.engine,
            "cloned": True,  # always true: this is a synthetic copy of a real person's voice; label it as such in UIs
            "consent_id": v.consent_id, "reference_seconds": round(v.ref_seconds, 1),
            "reference_quality": q, "similarity": v.similarity, "wer": v.wer, "synth_rtf": v.synth_rtf,
            "error": v.error or None, "created_at": v.created_at, "updated_at": v.updated_at,
            "usable_in_conversations": v.status == "ready",
            "notice": "Synthetic voice cloned from the replica owner's recording with their verified consent."}


def _set(s: Session, v: ReplicaVoice, **kw) -> None:
    for k, val in kw.items():
        setattr(v, k, val)
    v.updated_at = db.now()
    s.add(v)
    s.commit()


# ---------------------------------------------------------------- lifecycle
def request_voice(s: Session, account_id: str, rid: str, force: bool = False) -> ReplicaVoice:
    """Gate + queue. Raises CloneRefused. Idempotent: a ready/processing voice is returned unchanged unless force."""
    ensure_table()
    if not enabled():
        raise CloneRefused("voice cloning is disabled on this server (MIRAGE_VOICE_CLONE=0)", 503)
    rep = s.get(db.Replica, rid)
    if rep is None or rep.account_id != account_id:
        raise CloneRefused("replica not found", 404)
    if rep.status != "ready":
        raise CloneRefused("replica is not ready yet", 409)
    rec = require_consent(s, rid)
    if not (jobs.replica_dir(rid) / "voice_ref.wav").exists() and not (jobs.replica_dir(rid) / "source.mp4").exists():
        raise CloneRefused("the replica's training video has no usable audio track", 409)
    v = s.get(ReplicaVoice, rid)
    if v and v.status in ("ready", "processing", "queued") and not force:
        return v
    if v is None:
        v = ReplicaVoice(replica_id=rid, account_id=account_id)
    _set(s, v, consent_id=rec.id, status="queued", error="", engine=ENGINE, similarity=None, wer=None, synth_rtf=None)
    audit(s, account_id, "voice.clone_requested", rid, f"consent={rec.id}")
    return v


def process_voice(rid: str, sidecar=None, evaluate: bool = True) -> bool:
    """Blocking (seconds to a minute): extract a clean reference, register, and measure similarity / WER / RTF.
    Safe to call from the worker or a thread. Never raises."""
    ensure_table()
    with Session(db.engine) as s:
        v = s.get(ReplicaVoice, rid)
        if v is None:
            return False
        try:
            require_consent(s, rid)  # re-check: consent may have been revoked while queued
            _set(s, v, status="processing", error="")
            rd = jobs.replica_dir(rid)
            src = rd / "source.mp4"
            if not src.exists():
                src = rd / "voice_ref.wav"
            out = ref_path(rid)
            stats = refprep.prepare_reference(src, out)
            _set(s, v, ref_seconds=stats.seconds, ref_quality=json.dumps(stats.asdict()))
            if evaluate:
                res = evaluate_voice(rid, sidecar)
                if res["similarity"] is not None and res["similarity"] < MIN_SIMILARITY:
                    raise RuntimeError(f"cloned voice does not resemble the reference (similarity {res['similarity']:.2f} "
                                       f"< {MIN_SIMILARITY}); re-record with a cleaner, longer sample")
                _set(s, v, similarity=res["similarity"], wer=res["wer"], synth_rtf=res["rtf"])
            _set(s, v, status="ready")
            _forget(rid)
            audit(s, v.account_id, "voice.clone_ready", rid,
                  f"sim={v.similarity} wer={v.wer} rtf={v.synth_rtf} ref={stats.seconds:.1f}s")
            return True
        except CloneRefused as e:
            _purge_files(rid)
            _set(s, v, status="revoked", error=str(e))
            audit(s, v.account_id, "voice.clone_refused", rid, str(e)[:300])
        except Exception as e:  # noqa: BLE001
            log.warning("voice clone failed for %s: %s", rid, e)
            _purge_files(rid)
            _set(s, v, status="failed", error=f"{type(e).__name__}: {e}"[:500])
            audit(s, v.account_id, "voice.clone_failed", rid, f"{type(e).__name__}: {e}"[:300])
        return False


def evaluate_voice(rid: str, sidecar=None, text: str = TEST_SENTENCE) -> dict:
    """Synthesize `text` with the clone and measure: speaker similarity to the reference (WeSpeaker cosine), Whisper
    round-trip WER, and real-time factor. Similarity is None if the speaker model is missing."""
    import numpy as np

    from .. import consent_verify, voiceprint
    from .sidecar import SAMPLE_RATE, default_sidecar

    sc = sidecar or default_sidecar()
    t0 = time.time()
    pcm = sc.synth(text, ref_path(rid), "en")
    took = time.time() - t0
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    dur = len(audio) / SAMPLE_RATE
    if dur < 0.5:
        raise RuntimeError("clone produced no audio")
    tmp = voice_dir(rid) / "_eval.wav"
    refprep.write_wav(tmp, audio, SAMPLE_RATE)
    try:
        sim = None
        if voiceprint.available():
            try:
                sim = round(voiceprint.compare_files(ref_path(rid), tmp), 3)
            except Exception as e:  # noqa: BLE001
                log.info("similarity unavailable: %s", e)
        hyp = consent_verify.transcribe(voiceprint.decode_audio(tmp))
        w = round(wer(text, hyp), 3)
    finally:
        tmp.unlink(missing_ok=True)
    return {"similarity": sim, "wer": w, "rtf": round(took / dur, 2), "seconds": round(dur, 2), "heard": hyp}


def _purge_files(rid: str) -> int:
    d = voice_dir(rid)
    n = sum(1 for x in d.rglob("*") if x.is_file()) if d.exists() else 0
    shutil.rmtree(d, ignore_errors=True)
    return n


def delete_voice(s: Session, rid: str, account_id: str | None = None, reason: str = "deleted") -> bool:
    """Remove the stored voice data. reason='revoked' keeps a tombstone row (status revoked) so the API can explain why."""
    ensure_table()
    v = s.get(ReplicaVoice, rid)
    n = _purge_files(rid)
    if v is None:
        return n > 0
    acc = account_id or v.account_id
    if reason == "revoked":
        _set(s, v, status="revoked", ref_seconds=0.0, ref_quality="", similarity=None, wer=None, synth_rtf=None,
             error="consent revoked: voice data deleted")
    else:
        s.delete(v)
        s.commit()
    _forget(rid)
    audit(s, acc, f"voice.clone_{reason}", rid, f"{n} files removed")
    return True


# ---------------------------------------------------------------- synthesis-time gate (live + offline)
_cache: dict[str, tuple[float, Path | None]] = {}
_clock = threading.Lock()
CACHE_S = float(os.environ.get("MIRAGE_CLONE_GATE_CACHE_S", "5"))


def _forget(rid: str) -> None:
    with _clock:
        for k in [k for k in _cache if k.startswith(rid + "|")]:
            _cache.pop(k, None)


def usable_reference(rid: str, account_id: str | None = None) -> Path | None:
    """Reference wav if (and only if) this voice may be used right now: row ready, files present, consent still active,
    and (when account_id is given) the replica belongs to that account. Revoked consent => files are purged here too,
    even if the revoke endpoint's hook was bypassed. Cached for a few seconds (per-sentence calls)."""
    key = f"{rid}|{account_id or ''}"
    now = time.time()
    with _clock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_S:
            return hit[1]
    out: Path | None = None
    try:
        ensure_table()
        with Session(db.engine) as s:
            v = s.get(ReplicaVoice, rid)
            if v is not None and v.status == "ready" and (account_id is None or v.account_id == account_id):
                if active_consent(s, rid) is None:
                    delete_voice(s, rid, reason="revoked")
                elif ref_path(rid).exists():
                    out = ref_path(rid)
    except Exception as e:  # noqa: BLE001
        log.warning("clone gate error for %s: %s", rid, e)
        out = None
    if out is not None:  # only positive results are cached: a voice that just became ready is usable at once
        with _clock:
            _cache[key] = (now, out)
    return out


def audit_use(rid: str, detail: str) -> None:
    try:
        with Session(db.engine) as s:
            v = s.get(ReplicaVoice, rid)
            if v is not None:
                audit(s, v.account_id, "voice.clone_used", rid, detail[:300])
    except Exception:  # noqa: BLE001
        pass


def audit_fallback(rid: str, detail: str) -> None:
    try:
        with Session(db.engine) as s:
            v = s.get(ReplicaVoice, rid)
            if v is not None:
                audit(s, v.account_id, "voice.clone_fallback", rid, detail[:300])
    except Exception:  # noqa: BLE001
        pass


def validate_persona_voice(s: Session, account_id: str, voice: str) -> None:
    """For persona create/update: reject clone:<rid> unless it is a ready voice of the caller's own replica."""
    p = parse_voice(voice)
    if p is None:
        return
    rid = p[0]
    v = get_voice(s, rid)
    if v is None or v.account_id != account_id:
        raise CloneRefused("no such cloned voice on this account", 404)
    if v.status != "ready":
        raise CloneRefused(f"cloned voice is not ready (status: {v.status})", 409)


def auto_after_replica_ready(rid: str, sidecar=None) -> None:
    """Worker hook (after a replica turned ready): extract the reference and register + test the clone.
    Best effort, never raises, silent when cloning is disabled / the engine is not installed / consent does not qualify."""
    try:
        if not enabled() or os.environ.get("MIRAGE_VOICE_CLONE_AUTO", "1") == "0" or (sidecar is None and not engine_installed()):
            return
        with Session(db.engine) as s:
            rep = s.get(db.Replica, rid)
            if rep is None:
                return
            try:
                request_voice(s, rep.account_id, rid)
            except CloneRefused as e:
                log.info("auto voice clone skipped for %s: %s", rid, e)
                return
        process_voice(rid, sidecar)
    except Exception as e:  # noqa: BLE001
        log.warning("auto voice clone error for %s: %s", rid, e)
