import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session

from .. import db, jobs, settings, signing
from ..auth import current_account
from ..db import Account, Replica, Video, get_session
from ..models_extra import JobClaim, VideoMeta, ensure_tables

router = APIRouter()
_ID = re.compile(r"^[a-z]+_[0-9a-f]+$")


class VideoJobIn(BaseModel):
    replica_id: str
    script: str
    callback_url: str | None = None  # POSTed {event, video_id, status, output_url, error} when finished
    voice: str = "default"


@router.post("/video-jobs")
def create_video_job(body: VideoJobIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Like POST /v1/videos but with webhook + voice options (Video has no such columns)."""
    ensure_tables(db.engine)
    r = s.get(Replica, body.replica_id)
    if not r or r.account_id != acc.id:
        raise HTTPException(404, "replica not found")
    if r.status != "ready":
        raise HTTPException(409, "replica not ready")
    if body.callback_url and not body.callback_url.startswith(("http://", "https://")):
        raise HTTPException(422, "callback_url must be http(s)")
    v = Video(account_id=acc.id, replica_id=r.id, script=body.script)
    s.add(v); s.flush()
    s.add(VideoMeta(video_id=v.id, callback_url=body.callback_url, voice=body.voice))
    s.commit(); s.refresh(v)
    return v


@router.get("/jobs/{kind}/{ref_id}")
def job_status(kind: str, ref_id: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    ensure_tables(db.engine)
    model = {"replica": Replica, "video": Video}.get(kind)
    obj = s.get(model, ref_id) if model else None
    if not obj or obj.account_id != acc.id:
        raise HTTPException(404, "job not found")
    c = s.get(JobClaim, f"{kind}:{ref_id}")
    meta = s.get(VideoMeta, ref_id) if kind == "video" else None
    return {"kind": kind, "id": ref_id, "status": obj.status,
            "error": c.error if c else None, "attempts": c.attempts if c else 0,
            "detail": json.loads(c.detail) if c and c.detail else {},
            "webhook": meta.webhook_status if meta else None}


def _require_signature(request: Request, path: str) -> None:
    """Signed, expiring URLs (see signing.py). Unsigned access only when MIRAGE_ALLOW_PUBLIC_FILES is on
    (default: dev only)."""
    q = request.query_params
    if signing.verify(path, q.get("exp"), q.get("sig")):
        return
    if "sig" in q:  # a signature was given but is wrong/expired: never fall back to public access
        raise HTTPException(403, "invalid or expired file link")
    if not settings.allow_public_files():
        raise HTTPException(403, "signed link required; call POST /v1/files/sign")


@router.get("/files/videos/{name}")
def get_video_file(name: str, request: Request):
    vid = name.removesuffix(".mp4")
    if not _ID.match(vid) or not jobs.video_path(vid).exists():
        raise HTTPException(404, "not found")
    _require_signature(request, f"/v1/files/videos/{name}")
    return FileResponse(jobs.video_path(vid), media_type="video/mp4", headers={"Cache-Control": "private, max-age=300"})


@router.get("/files/replicas/{rid}/face.png")
def get_face(rid: str, request: Request):
    p = jobs.replica_dir(rid) / "face.png"
    if not _ID.match(rid) or not p.exists():
        raise HTTPException(404, "not found")
    _require_signature(request, f"/v1/files/replicas/{rid}/face.png")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})
