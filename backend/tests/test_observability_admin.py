import io
import json
import logging

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from app import admin, billing, db, metrics
from .test_api import client, signup  # noqa: F401


def test_request_id_header_and_inbound_honoured(client):
    r = client.get("/health")
    assert len(r.headers["x-request-id"]) >= 8
    r2 = client.get("/health", headers={"x-request-id": "trace-abc12345"})
    assert r2.headers["x-request-id"] == "trace-abc12345"
    r3 = client.get("/health", headers={"x-request-id": "bad id with spaces\n"})
    assert r3.headers["x-request-id"] != "bad id with spaces"


def test_metrics_endpoint_counts_routes_by_template_not_by_id(client, monkeypatch):
    monkeypatch.delenv("VOCALFACE_METRICS_TOKEN", raising=False)
    h = signup(client)
    p = client.post("/v1/personas", headers=h, json={"name": "a", "system_prompt": "s"}).json()
    client.put(f"/v1/personas/{p['id']}", headers=h, json={"name": "b", "system_prompt": "s"})
    client.get("/v1/replicas/r_nope", headers=h)
    body = client.get("/metrics").text
    assert 'vocalface_http_requests_total{method="PUT",route="/v1/personas/{pid}",status="200"}' in body
    assert 'route="/v1/replicas/{rid}",status="404"' in body
    assert p["id"] not in body  # no ids / keys as label values (cardinality + privacy)
    assert h["x-api-key"] not in body
    assert "vocalface_http_request_duration_seconds_bucket" in body and 'le="+Inf"' in body
    for name in ("vocalface_conversations_active", "vocalface_worker_queue_depth", "vocalface_webhook_deliveries", "vocalface_first_audio_window_ms_count",
                 "vocalface_worker_heartbeat_age_seconds", "vocalface_websocket_connections"):
        assert name in body, name


def test_metrics_token_protection(client, monkeypatch):
    monkeypatch.setenv("VOCALFACE_METRICS_TOKEN", "s3cret-token")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"authorization": "Bearer nope"}).status_code == 401
    assert client.get("/metrics", headers={"authorization": "Bearer s3cret-token"}).status_code == 200
    assert client.get("/metrics?token=s3cret-token").status_code == 200
    monkeypatch.delenv("VOCALFACE_METRICS_TOKEN")
    monkeypatch.setenv("VOCALFACE_ENV", "production")
    assert client.get("/metrics").status_code == 403  # production without a token: disabled, not open


def test_queue_depth_and_first_audio_from_db(client, monkeypatch):
    monkeypatch.delenv("VOCALFACE_METRICS_TOKEN", raising=False)
    h = signup(client)
    with Session(db.engine) as s:
        acc = s.exec(select(db.Account)).first()
        s.add(db.Video(account_id=acc.id, replica_id="r", script="x", status="queued"))
        s.add(db.Video(account_id=acc.id, replica_id="r", script="x", status="queued"))
        s.add(db.Replica(account_id=acc.id, name="r", train_video_url="u", status="training"))
        from app.models_features import TranscriptTurn
        for ms in (500, 700, 900, 1500):
            s.add(TranscriptTurn(conversation_id="c1", role="assistant", text="t", first_audio_ms=ms))
        s.commit()
    body = client.get("/metrics").text
    assert 'vocalface_worker_queue_depth{kind="video"} 2' in body and 'vocalface_worker_queue_depth{kind="replica"} 1' in body
    assert "vocalface_first_audio_window_ms_count 4" in body and 'quantile="0.5"' in body


def test_json_log_never_contains_secrets_or_query_strings(client, monkeypatch):
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(metrics.JsonFormatter())
    lg = logging.getLogger("vocalface.access")
    lg.addHandler(h); lg.setLevel(logging.INFO)
    try:
        key = signup(client)["x-api-key"]
        client.get(f"/v1/usage?api_key={key}&sig=zzz", headers={"x-api-key": key, "x-request-id": "req-00112233"})
        logging.getLogger("vocalface.test").warning("leaked key %s and whsec_abcdef123456", key)
    finally:
        lg.removeHandler(h)
    out = buf.getvalue()
    rec = [json.loads(l) for l in out.splitlines() if "/v1/usage" in l][0]
    assert rec["request_id"] == "req-00112233" and rec["route"] == "/v1/usage" and rec["status"] == 200 and "ms" in rec
    assert key not in out and "sig=zzz" not in out and "api_key" not in out
    assert len(rec["key_id"]) == 8  # hashed correlation id only
    # redaction of free-text messages
    f = metrics.JsonFormatter().format(logging.LogRecord("x", logging.WARNING, "", 0, "leaked key %s and whsec_abcdef123456", (key,), None))
    assert key not in f and "whsec_abcdef123456" not in f


def test_deep_health_db_critical_others_degrade(client, monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("VOCALFACE_LIPSYNC_URL", "http://127.0.0.1:1")
    r = client.get("/health/deep")
    j = r.json()
    assert r.status_code == 200 and j["ok"] is True and j["status"] == "degraded"
    assert j["checks"]["db"]["status"] == "ok" and j["checks"]["ollama"]["status"] == "down" and j["checks"]["lipsync"]["status"] == "down"
    assert j["checks"]["worker"]["status"] in ("never_seen", "stale", "ok") and "storage" in j["checks"]
    assert "ollama" in j["degraded"]
    monkeypatch.setattr(db, "engine", create_engine("sqlite:////nonexistent_dir/x.db"))
    r2 = client.get("/health/deep")
    assert r2.status_code == 503 and r2.json()["checks"]["db"]["status"] == "down"


def test_worker_heartbeat(tmp_path, monkeypatch):
    from app import jobs
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    assert metrics.worker_heartbeat_age() is None
    metrics.worker_heartbeat()
    assert 0 <= metrics.worker_heartbeat_age() < 2


# ---------------- admin CLI ----------------
@pytest.fixture
def admin_db(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path}/a.db")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db, "engine", eng)
    with Session(eng) as s:
        a = db.Account(email="ann@example.com", credits_seconds=100); s.add(a); s.commit()
        return a.id


def test_admin_grant_stats_accounts(admin_db, capsys):
    assert admin.main(["accounts"]) == 0
    out = capsys.readouterr().out
    assert "ann@example.com" in out and "mk_" not in out  # keys are never printed
    assert admin.main(["grant", "ann@example.com", "--minutes", "5", "--note", "support"]) == 0
    assert "balance 400s" in capsys.readouterr().out
    with Session(db.engine) as s:
        led = s.exec(select(billing.LedgerEntry)).all()
        assert led[0].kind == "grant" and led[0].seconds == 300 and led[0].ref.startswith("admin:")
    assert admin.main(["grant", admin_db, "--seconds", "-1000"]) == 0  # floors at zero
    assert "balance 0s" in capsys.readouterr().out
    assert admin.main(["--json", "stats"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["accounts"] == 1 and st["db"] == "sqlite"
    assert admin.main(["plan", admin_db, "pro"]) == 0 and "pro" in capsys.readouterr().out
    assert admin.main(["--json", "account", admin_db]) == 0
    assert json.loads(capsys.readouterr().out)["this_month"]["plan"]["id"] == "pro"
    assert admin.main(["billing-reset"]) == 0
    with pytest.raises(SystemExit):
        admin.main(["grant", "nobody@x.com", "--minutes", "1"])
