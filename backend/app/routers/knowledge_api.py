from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import knowledge as kb
from ..auth import current_account
from ..db import Account, Persona, get_session
from ..models_extra import KnowledgeDoc, MemorySummary, ensure_tables

router = APIRouter()
MAX_BYTES = 10 * 1024 * 1024


def _persona(s: Session, pid: str, acc: Account) -> Persona:
    p = s.get(Persona, pid)
    if not p or p.account_id != acc.id:
        raise HTTPException(404, "persona not found")
    from .. import db
    ensure_tables(db.engine)
    return p


def _doc(s: Session, pid: str, did: str) -> KnowledgeDoc:
    d = s.get(KnowledgeDoc, did)
    if not d or d.persona_id != pid:
        raise HTTPException(404, "document not found")
    return d


class TextDocIn(BaseModel):
    title: str
    text: str


@router.post("/personas/{pid}/knowledge/text")
def add_text(pid: str, body: TextDocIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    try:
        return kb.ingest(pid, body.title, body.text, "text", session=s)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/personas/{pid}/knowledge/upload")
async def upload(pid: str, file: UploadFile = File(...), title: str = Form(""),
                 acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (10 MB max)")
    name = file.filename or "document"
    is_pdf = name.lower().endswith(".pdf") or data[:5] == b"%PDF-"
    try:
        text = kb.extract_pdf_text(data) if is_pdf else data.decode("utf-8", errors="replace")
        return kb.ingest(pid, title or name, text, "pdf" if is_pdf else "file", session=s)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(422, f"could not read document: {e}")


@router.get("/personas/{pid}/knowledge")
def list_docs(pid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    return s.exec(select(KnowledgeDoc).where(KnowledgeDoc.persona_id == pid)).all()


@router.delete("/personas/{pid}/knowledge/{did}")
def delete_doc(pid: str, did: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    _doc(s, pid, did)
    kb.delete_doc(did, s)
    return {"deleted": did}


class SearchIn(BaseModel):
    query: str
    k: int = 4


@router.post("/personas/{pid}/knowledge/search")
def search(pid: str, body: SearchIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    return kb.retrieve(pid, body.query, min(max(body.k, 1), 20), session=s)


class MemoryIn(BaseModel):
    conversation_id: str | None = None
    summary: str | None = None
    turns: list[dict] | None = None  # [{role, content}] -> summarised (extractive; LLM hook in kb.summarize_turns)


@router.post("/personas/{pid}/memories")
def add_memory(pid: str, body: MemoryIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    summary = body.summary or (kb.summarize_turns(body.turns) if body.turns else "")
    if not summary:
        raise HTTPException(422, "provide summary or turns")
    return kb.save_memory(pid, summary, body.conversation_id, session=s)


@router.get("/personas/{pid}/memories")
def list_memories(pid: str, limit: int = 5, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    return kb.recent_memories(pid, min(limit, 50), session=s)
