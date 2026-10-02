import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app import db
from app.main import app
from app.pipeline.turn_taking import TurnTaker


@pytest.fixture()
def client():
    db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db.engine)

    def sess():
        from sqlmodel import Session
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    yield TestClient(app)
    app.dependency_overrides.clear()


def signup(c, email="a@b.com"):
    return {"x-api-key": c.post("/v1/signup", json={"email": email}).json()["api_key"]}


def test_requires_api_key(client):
    assert client.get("/v1/replicas").status_code == 422
    assert client.get("/v1/replicas", headers={"x-api-key": "bad"}).status_code == 401


def test_full_flow_and_credit_metering(client):
    h = signup(client)
    r = client.post("/v1/replicas", json={"name": "me", "train_video_url": "http://x/v.mp4"}, headers=h).json()
    p = client.post("/v1/personas", json={"name": "Sales", "system_prompt": "hi", "replica_id": r["id"]}, headers=h).json()
    c = client.post("/v1/conversations", json={"persona_id": p["id"]}, headers=h).json()
    ended = client.post(f"/v1/conversations/{c['id']}/end", headers=h).json()
    assert ended["status"] == "ended" and ended["seconds_used"] >= 1
    assert client.get("/v1/usage", headers=h).json()["credits_seconds"] < 600


def test_tenant_isolation(client):
    a, b = signup(client, "a@a.com"), signup(client, "b@b.com")
    r = client.post("/v1/replicas", json={"name": "x", "train_video_url": "u"}, headers=a).json()
    assert client.get(f"/v1/replicas/{r['id']}", headers=b).status_code == 404


def test_video_requires_ready_replica(client):
    h = signup(client)
    r = client.post("/v1/replicas", json={"name": "x", "train_video_url": "u"}, headers=h).json()
    assert client.post("/v1/videos", json={"replica_id": r["id"], "script": "hello"}, headers=h).status_code == 409


def test_turn_taking_detects_end_of_turn():
    import array
    loud = array.array("h", [3000] * 320).tobytes()
    quiet = bytes(640)
    t = TurnTaker(end_of_turn_ms=100)
    assert t.push(loud) == "speech"
    results = [t.push(quiet) for _ in range(6)]
    assert "end_of_turn" in results
