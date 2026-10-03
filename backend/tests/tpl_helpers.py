"""Shared fixtures/helpers for test_templates*.py, test_leads*.py, test_widget*.py (templates-leads module)."""
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import db, knowledge as kb, llm_backends as lb
from app.main import app


class FakeEmbedder:
    name = "fake:tpl"

    def embed(self, texts):
        v = np.array([[t.lower().count(w) for w in ["price", "plan", "hours", "leave", "room", "pets"]] for t in texts], dtype=np.float32) + 1e-3
        return v / np.linalg.norm(v, axis=1, keepdims=True)


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    from app import safety

    monkeypatch.setenv("MIRAGE_RL_SIGNUP", "1000/60")
    monkeypatch.setenv("MIRAGE_RL_IP", "100000/60")
    monkeypatch.setenv("MIRAGE_RL_KEY", "100000/60")
    monkeypatch.setenv("MIRAGE_WEBHOOK_LOOP", "0")
    safety.limiter.reset()
    lb.set_completer(None)
    lb.set_tool_post(None)
    yield
    lb.set_completer(None)
    lb.set_tool_post(None)
    safety.limiter.reset()


@pytest.fixture()
def client():
    db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db.engine)

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    kb.set_embedder(FakeEmbedder())
    yield TestClient(app)
    kb.set_embedder(None)
    app.dependency_overrides.clear()


def signup(c, email="a@b.com"):
    return {"x-api-key": c.post("/v1/signup", json={"email": email}).json()["api_key"]}


def make_persona(c, h, tid="customer-support", **body):
    r = c.post(f"/v1/templates/{tid}/instantiate", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def new_conversation(c, h, pid):
    r = c.post("/v1/conversations", json={"persona_id": pid}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]
