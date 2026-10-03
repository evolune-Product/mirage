"""POST /v1/files/sign: owner-checked signed URL for a replica face or rendered video."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import signing
from ..auth import current_account
from ..db import Account, Replica, Video, get_session
from ..safety import enforce_rate_limit

router = APIRouter()


class SignIn(BaseModel):
    path: str  # e.g. /v1/files/replicas/<id>/face.png or /v1/files/videos/<id>.mp4


@router.post("/files/sign")
def sign_file(body: SignIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    enforce_rate_limit(acc.api_key, 120, 60, "sign")
    path = body.path.split("?", 1)[0]
    if not signing.valid_file_path(path):
        raise HTTPException(422, "not a signable file path")
    parts = path.split("/")  # '', v1, files, kind, ...
    if parts[3] == "creative":  # /v1/files/creative/{replicas|videos}/<id>/<file> (routers/photo_replica.py)
        obj = s.get(Video if parts[4] == "videos" else Replica, parts[5])
    elif parts[3] == "videos":
        obj = s.get(Video, parts[4].removesuffix(".mp4"))
    else:
        obj = s.get(Replica, parts[4])
    if not obj or obj.account_id != acc.id:
        raise HTTPException(404, "file not found")
    from ..settings import signed_url_ttl
    return {"url": signing.sign_path(path), "expires_in": signed_url_ttl()}
