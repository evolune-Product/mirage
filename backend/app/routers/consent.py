import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import current_account
from ..db import Account, Replica, get_session
from ..models_billing import AuditLog, ConsentChallenge, ConsentRecord
from ..safety import audit, enforce_rate_limit, has_consent, moderate

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
    code = "-".join(secrets.choice(["amber", "river", "stone", "cedar", "lunar", "pixel", "ember", "north"]) for _ in range(3))
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
                        audio_sha256=hashlib.sha256(body.audio_url.encode()).hexdigest())
    s.add_all([ch, rec]); s.commit(); s.refresh(rec)
    audit(s, acc.id, "consent.recorded", rid, rec.id)
    s.refresh(rec)  # audit() commits, which expires rec
    return rec


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
    audit(s, acc.id, "consent.revoked", rid, f"{n} records")
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
