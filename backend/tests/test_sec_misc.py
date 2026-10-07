"""Secret scanning of logs/metrics/responses, API key scopes, CORS/CSRF review."""
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import db, hardening, jobs, safety, settings
from app.main import app


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool))
    SQLModel.metadata.create_all(db.engine)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setenv("VOCALFACE_SECRET_KEY", "test-secret-key-0123456789")
    monkeypatch.setenv("VOCALFACE_RL_SIGNUP", "1000/60")
    safety.limiter.reset()

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    yield TestClient(app)
    app.dependency_overrides.clear()
    safety.limiter.reset()


def signup(c):
    return c.post("/v1/signup", json={"email": "a@b.co"}).json()["api_key"]


def test_redact_covers_keys_query_params_and_bearer():
    t = settings.redact("GET /v1/x?api_key=zzzzzzzz1&sig=abcdef&token=t0k3n Bearer abcdefghijkl mk_abcdefghijk sk_live_abcdefgh")
    for leak in ("zzzzzzzz1", "abcdef&", "t0k3n", "abcdefghijkl", "abcdefghijk ", "live_abcdefgh"):
        assert leak not in t


def test_log_records_never_contain_api_keys_even_from_foreign_loggers(caplog):
    hardening.install_log_scrubbing()
    key = "mk_" + "A1b2C3d4E5" * 3
    caplog.set_level(logging.INFO)
    logging.getLogger("uvicorn.access").info('127.0.0.1 "GET /v1/conversations/c1/stream?api_key=%s HTTP/1.1" 101', key)
    logging.getLogger("whatever").warning("failed for key %s", key)
    try:
        raise RuntimeError(f"boom {key}")
    except RuntimeError:
        logging.getLogger("whatever").exception("x")
    from logging import Formatter
    out = "\n".join(Formatter().format(r) for r in caplog.records)
    assert key not in out and key[3:] not in out


def test_keys_not_in_responses_logs_metrics_or_audit(env, caplog):
    caplog.set_level(logging.DEBUG)
    key = signup(env)
    h = {"x-api-key": key}
    created = env.post("/v1/keys", json={"name": "k"}, headers=h).json()
    full = created["key"]
    env.get("/v1/keys", headers=h); env.get("/v1/usage", headers=h); env.get("/v1/audit", headers=h)
    env.get("/v1/usage", headers={"x-api-key": "mk_wrongwrongwrongwrong"})
    for path in ("/v1/keys", "/v1/audit", "/v1/usage"):
        body = env.get(path, headers=h).text
        assert full not in body  # a created key is shown exactly once
    assert full not in env.get("/metrics").text
    assert full not in caplog.text and key not in caplog.text
    listing = env.get("/v1/keys", headers=h).json()
    assert all("key_hash" not in k and "key" not in k for k in listing)


def test_read_only_key_cannot_write_or_delete(env):
    full = {"x-api-key": signup(env)}
    ro = env.post("/v1/keys", json={"name": "ro", "scope": "read"}, headers=full).json()
    assert ro["scope"] == "read"
    r = {"x-api-key": ro["key"]}
    assert env.get("/v1/usage", headers=r).status_code == 200
    assert env.get("/v1/keys", headers=r).status_code == 200
    for method, path, body in (("post", "/v1/replicas", {"name": "n", "train_video_url": "/x"}), ("post", "/v1/keys", {"name": "x"}),
                               ("post", "/v1/account/delete-my-data", {"confirm": "delete-my-data"}), ("delete", f"/v1/keys/{ro['id']}", None)):
        resp = getattr(env, method)(path, headers=r, **({"json": body} if body else {}))
        assert resp.status_code == 403 and resp.json()["detail"]["error"] == "read_only_key", (path, resp.text)
    assert env.post("/v1/files/sign", json={"path": "/v1/files/videos/v_ab.mp4"}, headers=r).status_code != 403  # allowed POST
    assert env.post("/v1/replicas", json={"name": "n", "train_video_url": "/x"}, headers=full).status_code == 200  # full key unaffected
    assert env.post("/v1/keys", json={"name": "x", "scope": "admin"}, headers=full).status_code == 422
    assert env.get("/v1/usage", headers={"x-api-key": full["x-api-key"]}).status_code == 200


def test_cors_allows_only_listed_origins_and_no_credentials(env, monkeypatch):
    r = env.options("/v1/usage", headers={"origin": "https://evil.example", "access-control-request-method": "GET",
                                          "access-control-request-headers": "x-api-key"})
    assert "access-control-allow-origin" not in r.headers
    ok = env.options("/v1/usage", headers={"origin": "http://localhost:3000", "access-control-request-method": "GET",
                                           "access-control-request-headers": "x-api-key"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-credentials" not in ok.headers  # keys travel in a header, never cookies


def test_csrf_not_applicable_no_cookie_auth_and_cross_site_form_post_fails(env):
    r = env.post("/v1/replicas", data={"name": "n", "train_video_url": "/x"}, headers={"origin": "https://evil.example"})
    assert r.status_code in (401, 422)  # no x-api-key header: a browser cannot add one cross-site without a CORS preflight
    k = signup(env)
    resp = env.get("/v1/usage", headers={"x-api-key": k})
    assert "set-cookie" not in resp.headers
