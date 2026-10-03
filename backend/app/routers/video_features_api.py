"""Video features: {{template variables}}, bulk generation from rows, translation/dubbing variants, batches."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import convo_runtime as cr, db, languages, llm_backends as lb
from ..auth import current_account
from ..db import Account, Replica, Video, get_session
from ..models_extra import VideoMeta, ensure_tables
from ..models_features import VideoBatch, VideoBatchItem
from ..safety import moderate_or_raise
from .. import webhooks

router = APIRouter()
MAX_ROWS = 200
MAX_LANGS = 10
MAX_SCRIPT = 5000


def _replica(s: Session, rid: str, acc: Account) -> Replica:
    r = s.get(Replica, rid)
    if not r or r.account_id != acc.id:
        raise HTTPException(404, "replica not found")
    if r.status != "ready":
        raise HTTPException(409, "replica not ready")
    return r


def render_script(template: str, row: dict) -> str:
    missing = [v for v in cr.template_vars(template) if v not in row]
    if missing:
        raise KeyError(missing)
    return cr.render(template, {k: str(v) for k, v in row.items()})


def _check_url(url: str | None):
    if url:
        try:
            webhooks.validate_url(url)
        except ValueError as e:
            raise HTTPException(422, f"callback_url: {e}")


def _blocklist_ok(text: str) -> bool:
    try:
        from .. import safety
        return not any(p.search(text) for p in safety._blocklist())
    except Exception:  # noqa: BLE001
        return True


class PreviewIn(BaseModel):
    script_template: str
    variables: dict[str, str] = {}


@router.post("/videos/template/preview")
def preview(body: PreviewIn, acc: Account = Depends(current_account)):
    needed = cr.template_vars(body.script_template)
    missing = [v for v in needed if v not in body.variables]
    return {"variables": needed, "missing": missing,
            "rendered": None if missing else cr.render(body.script_template, body.variables)}


class BulkIn(BaseModel):
    replica_id: str
    script_template: str = Field(min_length=1, max_length=MAX_SCRIPT)
    rows: list[dict[str, str]] = Field(min_length=1, max_length=MAX_ROWS)
    voice: str = "default"
    callback_url: str | None = None


def _make_videos(s: Session, acc: Account, rep: Replica, kind: str, items: list[dict], voice_for, callback_url) -> VideoBatch:
    ensure_tables(db.engine)
    cr.ensure()
    batch = VideoBatch(account_id=acc.id, kind=kind, replica_id=rep.id, total=len(items))
    s.add(batch); s.flush()
    for i, it in enumerate(items):
        v = Video(account_id=acc.id, replica_id=rep.id, script=it["script"])
        s.add(v); s.flush()
        s.add(VideoMeta(video_id=v.id, callback_url=callback_url, voice=voice_for(it)))
        s.add(VideoBatchItem(video_id=v.id, batch_id=batch.id, row_index=i, language=it.get("language", ""),
                             variables=json.dumps(it.get("variables", {})), rendered_script=it["script"]))
    s.commit(); s.refresh(batch)
    return batch


def _batch_out(s: Session, b: VideoBatch, with_items: bool = True) -> dict:
    items = s.exec(select(VideoBatchItem).where(VideoBatchItem.batch_id == b.id).order_by(VideoBatchItem.row_index)).all()
    rows, counts = [], {}
    for it in items:
        v = s.get(Video, it.video_id)
        st = v.status if v else "missing"
        counts[st] = counts.get(st, 0) + 1
        rows.append({"video_id": it.video_id, "row_index": it.row_index, "language": it.language, "status": st,
                     "output_url": v.output_url if v else None, "variables": json.loads(it.variables or "{}"),
                     "script": it.rendered_script})
    done = counts.get("ready", 0) + counts.get("error", 0)
    out = {"id": b.id, "kind": b.kind, "replica_id": b.replica_id, "total": b.total, "created_at": b.created_at,
           "counts": counts, "completed": done == b.total}
    if with_items:
        out["items"] = rows
    return out


@router.post("/video-jobs/bulk")
def bulk(body: BulkIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """One video per row; every {{variable}} in the template must be present in every row (422 lists the bad rows)."""
    rep = _replica(s, body.replica_id, acc)
    _check_url(body.callback_url)
    try:
        moderate_or_raise(cr.render(body.script_template, {v: "friend" for v in cr.template_vars(body.script_template)}))
    except HTTPException:
        raise
    items, problems = [], []
    for i, row in enumerate(body.rows):
        try:
            script = render_script(body.script_template, row)
        except KeyError as e:
            problems.append({"row": i, "missing": e.args[0]})
            continue
        if len(script) > MAX_SCRIPT or not _blocklist_ok(script):
            problems.append({"row": i, "error": "script too long or rejected by moderation"})
            continue
        items.append({"script": script, "variables": row})
    if problems:
        raise HTTPException(422, {"error": "invalid rows", "rows": problems[:50]})
    b = _make_videos(s, acc, rep, "bulk", items, lambda _: body.voice, body.callback_url)
    return _batch_out(s, b)


class TranslateIn(BaseModel):
    replica_id: str
    script: str = Field(min_length=1, max_length=MAX_SCRIPT)
    languages: list[str] = Field(min_length=1, max_length=MAX_LANGS)
    source_language: str = "en"
    voices: dict[str, str] = {}  # optional per-language Kokoro voice override
    include_original: bool = False
    callback_url: str | None = None


_translator_model = None


async def translate_text(text: str, src: str, dst: str) -> str:
    import os

    names = {**{k: v["name"] for k, v in languages.LANGUAGES.items()}, **languages.STT_ONLY}
    backend = lb.make_backend("", model=os.environ.get("MIRAGE_TRANSLATE_MODEL", "qwen3:8b"))
    backend.num_predict = 600
    prompt = (f"Translate the following video script from {names.get(src, src)} to {names.get(dst, dst)}. Keep the meaning, "
              f"tone and length; keep names unchanged. Output ONLY the translation, no quotes or notes.\n\n{text}")
    out = await lb.complete(backend, "You are a professional translator for spoken video scripts.", prompt)
    out = out.strip().strip('"').strip()
    if not out:
        raise RuntimeError("empty translation")
    return out


@router.post("/video-jobs/translate")
async def translate(body: TranslateIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Translate the script with the LLM into each target language and queue one video per language, spoken with
    that language's voice. NOTE: lip movement is audio-driven, so each variant is re-rendered (not re-timed)."""
    rep = _replica(s, body.replica_id, acc)
    _check_url(body.callback_url)
    moderate_or_raise(body.script)
    try:
        src = languages.normalize_language(body.source_language)
        targets = []
        for code in body.languages:
            c = languages.normalize_language(code)
            if c == languages.AUTO:
                raise ValueError("'auto' is not a valid target language")
            if c not in languages.LANGUAGES:
                raise ValueError(f"no voice available for '{c}' (supported: {', '.join(languages.LANGUAGES)})")
            if c not in targets:
                targets.append(c)
    except ValueError as e:
        raise HTTPException(422, str(e))
    items = []
    if body.include_original:
        items.append({"script": body.script, "language": src})
    for code in targets:
        if code == src:
            continue
        try:
            text = await translate_text(body.script, src, code)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"translation to '{code}' failed: {type(e).__name__}: {e}"[:300])
        if not _blocklist_ok(text):
            raise HTTPException(422, f"translation to '{code}' was rejected by moderation")
        items.append({"script": text, "language": code})
    if not items:
        raise HTTPException(422, "nothing to render (target languages equal the source language)")
    voice_for = lambda it: body.voices.get(it["language"]) or languages.default_voice(it["language"])  # noqa: E731
    b = _make_videos(s, acc, rep, "translate", items, voice_for, body.callback_url)
    return _batch_out(s, b)


@router.get("/video-batches")
def list_batches(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cr.ensure()
    return [_batch_out(s, b, with_items=False) for b in s.exec(
        select(VideoBatch).where(VideoBatch.account_id == acc.id).order_by(VideoBatch.created_at.desc())).all()]


@router.get("/video-batches/{bid}")
def get_batch(bid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cr.ensure()
    b = s.get(VideoBatch, bid)
    if not b or b.account_id != acc.id:
        raise HTTPException(404, "batch not found")
    return _batch_out(s, b)
