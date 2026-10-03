"""Photo avatars, replica backgrounds and creative assets (uploaded background images / logos).

POST   /v1/replicas/photo                  {name, photo_url, idle_seconds?, head_motion?}  -> replica (awaiting_consent), like POST /v1/replicas
GET    /v1/replicas/{id}/photo             photo-replica status (queued|animating|ready|error), warnings, error, animate seconds
GET    /v1/replicas/{id}/background        default background of the replica (live conversations + videos), 404 if none
POST   /v1/replicas/{id}/background        {type: color|gradient|image|blur|none, ...}  applied to the live lip-sync service too
DELETE /v1/replicas/{id}/background
POST   /v1/creative/assets                 multipart (file, kind=background|logo) or JSON {url, kind}  -> {id, kind, width, height}
GET    /v1/creative/assets                 list; DELETE /v1/creative/assets/{id}
GET    /v1/creative/options                supported formats, styles, transitions, ranges (for UIs)
GET    /v1/files/creative/replicas/{id}/idle.mp4 | video-extras/{vid}/{thumbnail.jpg|captions.srt}   (signed, see /v1/files/sign)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import creative_options as co, db, jobs, settings, signing
from ..auth import current_account
from ..db import Account, Replica, Video, get_session
from ..models_creative import CreativeAsset, PhotoReplica, ReplicaBackground, VideoOptions
from ..models_extra import ensure_tables

router = APIRouter()
MAX_ASSET_BYTES = 15 * 1024 * 1024
_ID = re.compile(r"^[a-z]+_[0-9a-f]+$")
_IMG_EXT = {"png": "png", "jpeg": "jpg", "jpg": "jpg", "webp": "webp"}


def assets_dir() -> Path:
    d = jobs.DATA_DIR / "creative_assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tables():
    ensure_tables(db.engine)


def _own_replica(s: Session, rid: str, acc: Account) -> Replica:
    r = s.get(Replica, rid)
    if not r or r.account_id != acc.id:
        raise HTTPException(404, "replica not found")
    return r


def resolver_for(s: Session, acc: Account) -> co.Resolver:
    """asset_id -> file path (owner + kind checked)."""
    def resolve(aid: str, kind: str) -> Path:
        a = s.get(CreativeAsset, aid)
        if not a or a.account_id != acc.id:
            raise ValueError(f"asset {aid} not found")
        if a.kind not in (kind, "image"):
            raise ValueError(f"asset {aid} is a {a.kind}, expected {kind}")
        p = assets_dir() / f"{a.id}.{a.ext}"
        if not p.exists():
            raise ValueError(f"asset {aid} file is missing")
        return p

    return resolve


# ---------------------------------------------------------------- photo replica
class PhotoReplicaIn(BaseModel):
    name: str
    photo_url: str
    idle_seconds: float = Field(default=4.0, ge=2.0, le=8.0)
    head_motion: float = Field(default=1.0, ge=0.0, le=2.0)


@router.post("/replicas/photo")
def create_photo_replica(body: PhotoReplicaIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Create a replica from ONE portrait photo. The worker validates the photo (one face, mouth closed, eyes open),
    animates it into a short idle clip with LivePortrait (minutes, one time) and stores it as the idle/listening clip.
    Consent is required exactly like for video replicas (the worker does not start before a consent record exists)."""
    _tables()
    if not body.photo_url.startswith(("http://", "https://")) and settings.is_production():
        raise HTTPException(422, "photo_url must be http(s)")
    if body.photo_url.startswith(("http://", "https://")):
        from .. import netguard  # SSRF: refuse private/metadata targets up front (the fetch re-checks, pinned)

        try:
            netguard.check_url(body.photo_url)
        except ValueError as e:
            raise HTTPException(422, f"photo_url refused: {e}")
    r = Replica(account_id=acc.id, name=body.name, train_video_url=body.photo_url, status="awaiting_consent")
    s.add(r); s.flush()
    s.add(PhotoReplica(replica_id=r.id, photo_url=body.photo_url, idle_seconds=body.idle_seconds, head_motion=body.head_motion))
    s.commit(); s.refresh(r)
    return r


@router.get("/replicas/{rid}/photo")
def get_photo_replica(rid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _tables()
    r = _own_replica(s, rid, acc)
    pr = s.get(PhotoReplica, rid)
    if not pr:
        raise HTTPException(404, "not a photo replica")
    return {"replica_id": rid, "replica_status": r.status, "status": pr.status, "error": pr.error or None,
            "warnings": json.loads(pr.warnings or "[]"), "idle_seconds": pr.idle_seconds, "head_motion": pr.head_motion,
            "animate_s": pr.animate_s,
            "idle_url": f"/v1/files/creative/replicas/{rid}/idle.mp4" if (jobs.replica_dir(rid) / "listening.mp4").exists() and pr.status == "ready" else None}


# ---------------------------------------------------------------- backgrounds
def _lipsync_invalidate(rid: str) -> None:
    """Best effort: tell the live lip-sync service to rebuild this replica's base with the new background."""
    try:
        httpx.post(f"{os.environ.get('MIRAGE_LIPSYNC_URL', 'http://localhost:8100').rstrip('/')}/invalidate/{rid}", timeout=2)
    except Exception:  # noqa: BLE001
        pass


def _bg_file(rid: str) -> Path:
    return jobs.replica_dir(rid) / "background.json"


@router.post("/replicas/{rid}/background")
def set_background(rid: str, body: dict, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Default background for the replica: used by the live lip-sync service (segmentation runs ONCE when the base clip is
    prepared, so live fps is unchanged) and by every offline video of this replica (unless the video sets its own)."""
    _tables()
    _own_replica(s, rid, acc)
    try:
        spec = co.validate_background(body, resolver_for(s, acc))
    except ValueError as e:
        raise HTTPException(422, str(e))
    row = s.get(ReplicaBackground, rid) or ReplicaBackground(replica_id=rid)
    row.spec = json.dumps(spec or {})
    row.updated_at = db.now()
    s.add(row); s.commit()
    d = jobs.replica_dir(rid)
    if d.exists():
        if spec:
            _bg_file(rid).write_text(json.dumps(spec))
        else:
            _bg_file(rid).unlink(missing_ok=True)
        _lipsync_invalidate(rid)
    return {"replica_id": rid, "background": spec}


@router.get("/replicas/{rid}/background")
def get_background(rid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _tables()
    _own_replica(s, rid, acc)
    row = s.get(ReplicaBackground, rid)
    spec = json.loads(row.spec) if row else None
    if not spec:
        raise HTTPException(404, "no background set")
    return {"replica_id": rid, "background": {k: v for k, v in spec.items() if k != "path"}}


@router.delete("/replicas/{rid}/background")
def delete_background(rid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _tables()
    _own_replica(s, rid, acc)
    row = s.get(ReplicaBackground, rid)
    if not row or not json.loads(row.spec or "{}"):
        raise HTTPException(404, "no background set")
    s.delete(row); s.commit()
    _bg_file(rid).unlink(missing_ok=True)
    _lipsync_invalidate(rid)
    return {"deleted": True}


# ---------------------------------------------------------------- assets
def _sniff_image(data: bytes) -> tuple[str, int, int]:
    from PIL import Image
    import io

    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "not a readable image (PNG, JPEG or WebP)")
    ext = _IMG_EXT.get((im.format or "").lower())
    if not ext:
        raise HTTPException(422, "unsupported image type (PNG, JPEG or WebP)")
    return ext, im.width, im.height


def _store_asset(s: Session, acc: Account, kind: str, filename: str, data: bytes) -> CreativeAsset:
    if kind not in ("background", "logo", "image"):
        raise HTTPException(422, "kind must be background, logo or image")
    if len(data) > MAX_ASSET_BYTES:
        raise HTTPException(413, "image larger than 15 MB")
    ext, w, h = _sniff_image(data)
    if min(w, h) < 16 or max(w, h) > 8000:
        raise HTTPException(422, "image dimensions must be between 16 and 8000 px")
    a = CreativeAsset(account_id=acc.id, kind=kind, filename=os.path.basename(filename or "")[:120], ext=ext,
                      sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), width=w, height=h)
    (assets_dir() / f"{a.id}.{ext}").write_bytes(data)
    s.add(a); s.commit(); s.refresh(a)
    return a


def _asset_out(a: CreativeAsset) -> dict:
    return {"id": a.id, "kind": a.kind, "filename": a.filename, "width": a.width, "height": a.height, "bytes": a.bytes,
            "created_at": a.created_at}


@router.post("/creative/assets")
async def upload_asset(request: Request, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """multipart/form-data (file, kind) or application/json {url, kind}. http(s) URLs only (local paths in dev)."""
    _tables()
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/"):
        form = await request.form()
        up = form.get("file")
        if up is None or not hasattr(up, "read"):
            raise HTTPException(422, "file is required")
        data = await up.read(MAX_ASSET_BYTES + 1)
        a = _store_asset(s, acc, str(form.get("kind", "image")), getattr(up, "filename", ""), data)
        return _asset_out(a)
    try:
        body = await request.json()
        url, kind = str(body["url"]), str(body.get("kind", "image"))
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "send multipart (file, kind) or JSON {url, kind}")
    from .. import consent_verify

    tmp = Path(tempfile.mkstemp(prefix="asset_")[1])
    try:
        try:
            consent_verify.fetch_train_video(url, tmp)  # SSRF-guarded http(s) fetch (+ local path in dev), size capped
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"could not fetch the image: {type(e).__name__}")
        a = _store_asset(s, acc, kind, os.path.basename(url.split("?")[0]), tmp.read_bytes())
    finally:
        tmp.unlink(missing_ok=True)
    return _asset_out(a)


@router.get("/creative/assets")
def list_assets(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _tables()
    return [_asset_out(a) for a in s.exec(select(CreativeAsset).where(CreativeAsset.account_id == acc.id).order_by(CreativeAsset.created_at.desc())).all()]


@router.delete("/creative/assets/{aid}")
def delete_asset(aid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _tables()
    a = s.get(CreativeAsset, aid)
    if not a or a.account_id != acc.id:
        raise HTTPException(404, "asset not found")
    (assets_dir() / f"{a.id}.{a.ext}").unlink(missing_ok=True)
    s.delete(a); s.commit()
    return {"deleted": True}


@router.get("/creative/options")
def creative_options(acc: Account = Depends(current_account)):
    return {"formats": list(co.ASPECTS), "resolutions": list(co.RESOLUTIONS), "caption_styles": list(co.CAPTION_STYLES),
            "transitions": list(co.TRANSITIONS), "logo_positions": list(co.LOGO_POSITIONS),
            "background_types": [t for t in co.BG_TYPES if t != "none"],
            "limits": {"max_scenes": 12, "max_script_chars": 5000, "max_asset_mb": 15}}


# ---------------------------------------------------------------- signed file serving
def _check_sig(request: Request, path: str) -> None:
    q = request.query_params
    if signing.verify(path, q.get("exp"), q.get("sig")):
        return
    if "sig" in q:
        raise HTTPException(403, "invalid or expired file link")
    if not settings.allow_public_files():
        raise HTTPException(403, "signed link required; call POST /v1/files/sign")


@router.get("/files/creative/replicas/{rid}/idle.mp4")
def idle_file(rid: str, request: Request):
    p = jobs.replica_dir(rid) / "listening.mp4"
    if not _ID.match(rid) or not p.exists():
        raise HTTPException(404, "not found")
    _check_sig(request, f"/v1/files/creative/replicas/{rid}/idle.mp4")
    return FileResponse(p, media_type="video/mp4", headers={"Cache-Control": "private, max-age=300"})


@router.get("/files/creative/videos/{vid}/{name}")
def video_extra(vid: str, name: str, request: Request):
    ext = {"thumbnail.jpg": ("jpg", "image/jpeg"), "captions.srt": ("srt", "application/x-subrip")}.get(name)
    if not ext or not _ID.match(vid):
        raise HTTPException(404, "not found")
    p = jobs.video_path(vid).with_suffix("." + ext[0])
    if not p.exists():
        raise HTTPException(404, "not found")
    _check_sig(request, f"/v1/files/creative/videos/{vid}/{name}")
    return FileResponse(p, media_type=ext[1], headers={"Cache-Control": "private, max-age=300"})
