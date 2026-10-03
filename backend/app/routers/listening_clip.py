"""Optional closed-mouth 'listening' clip per replica.

POST   /v1/replicas/{id}/listening-clip   {url}  -> stores <data>/replicas/<id>/listening.mp4 (+ metadata row)
GET    /v1/replicas/{id}/listening-clip         -> metadata (404 if none)
DELETE /v1/replicas/{id}/listening-clip

The lip-sync service uses it as the idle loop and as the base for speaking segments; without it, it falls back
to the lowest-motion window of the source video."""
import json
import os
import subprocess
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import jobs
from ..auth import current_account
from ..db import Account, Replica, get_session, now
from ..models_face import ListeningClip

router = APIRouter()

MIN_S, MAX_S = 1.0, 120.0
MAX_BYTES = 200 * 1024 * 1024


class ListeningIn(BaseModel):
    url: str


def clip_path(rid: str) -> Path:
    return jobs.replica_dir(rid) / "listening.mp4"


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate,duration:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise ValueError("not a readable video")
    j = json.loads(r.stdout or "{}")
    st = (j.get("streams") or [None])[0]
    if not st:
        raise ValueError("no video stream")
    num, _, den = (st.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    dur = float(st.get("duration") or (j.get("format") or {}).get("duration") or 0)
    return {"width": int(st["width"]), "height": int(st["height"]), "fps": round(fps, 3), "duration_s": round(dur, 3)}


def _owned_replica(s: Session, rid: str, acc: Account) -> Replica:
    r = s.get(Replica, rid)
    if not r or r.account_id != acc.id:
        raise HTTPException(404, "replica not found")
    return r


def _notify_lipsync(rid: str) -> None:
    """Best effort: tell the lip-sync service to drop its cached base for this replica."""
    url = os.environ.get("MIRAGE_LIPSYNC_URL", "http://localhost:8100").rstrip("/")
    try:
        httpx.post(f"{url}/invalidate/{rid}", timeout=1.5)
    except Exception:
        pass


def _row(c: ListeningClip) -> dict:
    return {"replica_id": c.replica_id, "source_url": c.source_url, "duration_s": c.duration_s, "width": c.width,
            "height": c.height, "fps": c.fps, "bytes": c.bytes, "updated_at": c.updated_at or c.created_at}


@router.post("/replicas/{rid}/listening-clip")
def set_listening_clip(rid: str, body: ListeningIn, acc: Account = Depends(current_account),
                       s: Session = Depends(get_session)):
    _owned_replica(s, rid, acc)
    dest = clip_path(rid)
    tmp = dest.with_suffix(".tmp.mp4")
    try:
        jobs.fetch_video(body.url, tmp)
    except FileNotFoundError:
        tmp.unlink(missing_ok=True)
        raise HTTPException(422, "listening clip not found at url")
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(422, f"could not download listening clip: {e}")
    try:
        if tmp.stat().st_size > MAX_BYTES:
            raise ValueError("file too large (max 200 MB)")
        info = probe(tmp)
        if not (MIN_S <= info["duration_s"] <= MAX_S):
            raise ValueError(f"clip must be {MIN_S:.0f}-{MAX_S:.0f} s long (got {info['duration_s']:.1f} s)")
    except (ValueError, subprocess.SubprocessError) as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(422, str(e))
    tmp.replace(dest)
    row = s.get(ListeningClip, rid) or ListeningClip(replica_id=rid)
    row.source_url, row.bytes, row.updated_at = body.url, dest.stat().st_size, now()
    row.duration_s, row.width, row.height, row.fps = info["duration_s"], info["width"], info["height"], info["fps"]
    s.add(row); s.commit(); s.refresh(row)
    _notify_lipsync(rid)
    return _row(row)


@router.get("/replicas/{rid}/listening-clip")
def get_listening_clip(rid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _owned_replica(s, rid, acc)
    row = s.get(ListeningClip, rid)
    if not row or not clip_path(rid).exists():
        raise HTTPException(404, "no listening clip for this replica")
    return _row(row)


@router.delete("/replicas/{rid}/listening-clip")
def delete_listening_clip(rid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _owned_replica(s, rid, acc)
    row = s.get(ListeningClip, rid)
    existed = bool(row) or clip_path(rid).exists()
    if row:
        s.delete(row); s.commit()
    clip_path(rid).unlink(missing_ok=True)
    _notify_lipsync(rid)
    if not existed:
        raise HTTPException(404, "no listening clip for this replica")
    return {"deleted": True}
