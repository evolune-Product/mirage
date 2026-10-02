from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlmodel import Session

from .db import Account, get_session, init_db
from .routers import resources


@asynccontextmanager
async def lifespan(_):
    init_db()
    yield


app = FastAPI(title="Mirage API", version="0.1.0", lifespan=lifespan)
app.include_router(resources.router, prefix="/v1")


class SignupIn(BaseModel):
    email: str


@app.post("/v1/signup")
def signup(body: SignupIn, s: Session = Depends(get_session)):
    acc = Account(email=body.email)
    s.add(acc); s.commit(); s.refresh(acc)
    return {"account_id": acc.id, "api_key": acc.api_key, "credits_seconds": acc.credits_seconds}


@app.get("/health")
def health():
    return {"ok": True}
