from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from .. import languages
from ..auth import current_account
from ..db import Account, Replica, get_session

router = APIRouter()


def _cloned_voices(s: Session, acc: Account) -> list[dict]:
    """Voices cloned from this account's own replicas (consent-gated; see voice_clone.service)."""
    from ..models_voice import ReplicaVoice
    from ..voice_clone import service

    service.ensure_table()
    names = {r.id: r.name for r in s.exec(select(Replica).where(Replica.account_id == acc.id)).all()}
    out = []
    for v in s.exec(select(ReplicaVoice).where(ReplicaVoice.account_id == acc.id, ReplicaVoice.status == "ready")).all():
        out.append({"id": service.voice_id(v.replica_id), "name": f"Cloned voice: {names.get(v.replica_id, v.replica_id)}",
                    "language": "multi", "engine": service.ENGINE, "cloned": True, "replica_id": v.replica_id,
                    "similarity": v.similarity, "default_for_language": False,
                    "notice": "Synthetic voice cloned from the replica owner's recording with verified consent."})
    return out


@router.get("/voices")
def voices(language: str | None = None, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cat = languages.catalogue()
    if language:
        try:
            code = languages.normalize_language(language)
        except ValueError:
            code = language
        cat["voices"] = [v for v in cat["voices"] if v["language"] == code]
    try:
        cat["voices"] = cat["voices"] + _cloned_voices(s, acc)  # cloned voices speak any language the cloner supports
    except Exception:  # noqa: BLE001 - never break the catalogue because of the optional feature
        pass
    return cat


@router.get("/languages")
def list_languages(acc: Account = Depends(current_account)):
    return languages.catalogue()["languages"]
