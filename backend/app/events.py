"""Lifecycle webhook events emitted from the job worker (replica/video finished). Never raises."""
from __future__ import annotations

from sqlmodel import Session, select

from . import db, webhooks


def on_replica_finished(rid: str) -> None:
    try:
        with Session(db.engine) as s:
            r = s.get(db.Replica, rid)
            if r is None:
                return
            ev = "replica.ready" if r.status == "ready" else "replica.error"
            webhooks.emit(r.account_id, ev, {"replica_id": rid, "name": r.name, "status": r.status}, session=s)
    except Exception:  # noqa: BLE001
        pass


def on_video_finished(vid: str, error: str | None = None) -> None:
    try:
        from .models_features import VideoBatch, VideoBatchItem

        webhooks.ensure()
        with Session(db.engine) as s:
            v = s.get(db.Video, vid)
            if v is None:
                return
            item = s.get(VideoBatchItem, vid)
            data = {"video_id": vid, "status": v.status, "output_url": v.output_url, "error": error}
            if item:
                data.update(batch_id=item.batch_id, language=item.language, row_index=item.row_index)
            webhooks.emit(v.account_id, "video.ready" if v.status == "ready" else "video.error", data, session=s)
            if item:
                items = s.exec(select(VideoBatchItem).where(VideoBatchItem.batch_id == item.batch_id)).all()
                vids = [s.get(db.Video, i.video_id) for i in items]
                if all(x and x.status in ("ready", "error") for x in vids):
                    b = s.get(VideoBatch, item.batch_id)
                    webhooks.emit(v.account_id, "video_batch.completed", {
                        "batch_id": item.batch_id, "total": len(vids), "ready": sum(1 for x in vids if x.status == "ready"),
                        "errors": sum(1 for x in vids if x.status == "error"), "kind": b.kind if b else ""}, session=s)
    except Exception:  # noqa: BLE001
        pass
