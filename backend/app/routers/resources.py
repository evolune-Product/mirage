from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import current_account
from ..billing import record_usage
from ..safety import moderate_or_raise
from ..db import Account, Conversation, Persona, Replica, Video, get_session

router = APIRouter()


def owned(session: Session, model, id: str, acc: Account):
    obj = session.get(model, id)
    if not obj or obj.account_id != acc.id:
        raise HTTPException(404, f"{model.__name__.lower()} not found")
    return obj


# ---- replicas ----
class ReplicaIn(BaseModel):
    name: str
    train_video_url: str


@router.post("/replicas")
def create_replica(body: ReplicaIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    r = Replica(account_id=acc.id, status="awaiting_consent", **body.model_dump())
    s.add(r); s.commit(); s.refresh(r)
    return r  # training job is picked up by workers/


@router.get("/replicas")
def list_replicas(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    return s.exec(select(Replica).where(Replica.account_id == acc.id)).all()


@router.get("/replicas/{rid}")
def get_replica(rid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    return owned(s, Replica, rid, acc)


# ---- personas ----
class PersonaIn(BaseModel):
    name: str
    system_prompt: str
    replica_id: str | None = None
    llm: str = "ollama/llama3.2:1b"
    tts_voice: str = "default"
    knowledge: str = ""


@router.post("/personas")
def create_persona(body: PersonaIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    if body.replica_id:
        owned(s, Replica, body.replica_id, acc)
    p = Persona(account_id=acc.id, **body.model_dump())
    s.add(p); s.commit(); s.refresh(p)
    return p


@router.put("/personas/{pid}")
def update_persona(pid: str, body: PersonaIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    p = owned(s, Persona, pid, acc)
    if body.replica_id:
        owned(s, Replica, body.replica_id, acc)
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    s.add(p); s.commit(); s.refresh(p)
    return p


@router.get("/personas")
def list_personas(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    return s.exec(select(Persona).where(Persona.account_id == acc.id)).all()


# ---- conversations ----
class ConversationIn(BaseModel):
    persona_id: str
    # optional (feature layer): memory scope, extra context, {{variables}}, hard time cap, language override
    participant_id: str = ""
    context: str = ""
    variables: dict[str, str] = {}
    max_seconds: int | None = None
    language: str | None = None


@router.post("/conversations")
def create_conversation(body: ConversationIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    owned(s, Persona, body.persona_id, acc)
    if acc.credits_seconds <= 0:
        raise HTTPException(402, "out of credits")
    c = Conversation(account_id=acc.id, persona_id=body.persona_id)
    c.room_url = f"/rooms/{c.id}"  # replaced by LiveKit room URL once media server is wired
    from ..convo_runtime import on_conversation_created

    on_conversation_created(s, c, body.model_dump(exclude={"persona_id"}))  # validates options, emits conversation.started
    s.add(c); s.commit(); s.refresh(c)
    return c


@router.post("/conversations/{cid}/end")
def end_conversation(cid: str, background: BackgroundTasks, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    c = owned(s, Conversation, cid, acc)
    if c.status == "ended":
        return c
    c.status, c.ended_at = "ended", datetime.now(timezone.utc)
    started = c.started_at.replace(tzinfo=timezone.utc) if c.started_at.tzinfo is None else c.started_at
    c.seconds_used = max(int((c.ended_at - started).total_seconds()), 1)
    acc.credits_seconds = max(acc.credits_seconds - c.seconds_used, 0)
    s.add_all([c, acc]); s.commit(); s.refresh(c)
    record_usage(s, acc, c.seconds_used, f"conv:{c.id}")
    s.refresh(c)  # record_usage commits, which expires c
    from ..convo_runtime import finalize_conversation

    background.add_task(finalize_conversation, c.id, "ended")  # summary -> memory, metrics, webhooks
    return c


@router.get("/conversations")
def list_conversations(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    return s.exec(select(Conversation).where(Conversation.account_id == acc.id)).all()


# ---- videos ----
class VideoIn(BaseModel):
    replica_id: str
    script: str


@router.post("/videos")
def create_video(body: VideoIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    r = owned(s, Replica, body.replica_id, acc)
    if r.status != "ready":
        raise HTTPException(409, "replica not ready")
    moderate_or_raise(body.script)
    v = Video(account_id=acc.id, **body.model_dump())
    s.add(v); s.commit(); s.refresh(v)
    return v


@router.get("/videos")
def list_videos(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    return s.exec(select(Video).where(Video.account_id == acc.id)).all()


@router.get("/videos/{vid}")
def get_video(vid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    return owned(s, Video, vid, acc)


# ---- usage ----
@router.get("/usage")
def usage(acc: Account = Depends(current_account)):
    return {"credits_seconds": acc.credits_seconds}
