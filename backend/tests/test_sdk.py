import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))
from vocalface_sdk import VocalFace, VocalFaceError  # noqa: E402

from .test_api import client  # noqa: E402,F401


def test_sdk_roundtrip(client):
    m = VocalFace(http_client=client)
    assert m.signup("sdk@x.com")["api_key"].startswith("mk_")
    assert m.usage()["credits_seconds"] == 600
    assert "plans" in m.billing_plans() and m.billing_status()["plan"]["id"] == "free"
    r = m.create_replica("Ann", "http://x/v.mp4")
    assert m.get_replica(r["id"])["name"] == "Ann" and len(m.list_replicas()) == 1
    with pytest.raises(VocalFaceError) as e:
        m.create_video(r["id"], "hi")
    assert e.value.status == 409
    ch = m.consent_challenge(r["id"])
    m.record_consent(r["id"], ch["challenge_id"], "Ann", "http://x/a.wav", ch["phrase"])
    assert m.get_consent(r["id"])["has_consent"]
    p = m.create_persona("Bot", "be nice", replica_id=r["id"])
    assert len(m.list_personas()) == 1
    m.add_knowledge_text(p["id"], "t", "VocalFace is a CVI platform.")
    assert len(m.list_knowledge(p["id"])) == 1
    m.search_knowledge(p["id"], "what is vocalface")
    m.upload_knowledge(p["id"], "a.txt", b"hello world doc")
    m.add_memory(p["id"], summary="s")
    m.list_memories(p["id"])
    c = m.create_conversation(p["id"])
    assert m.end_conversation(c["id"])["status"] == "ended"
    assert m.moderate("hello")["allowed"]
    assert [e["kind"] for e in m.usage_ledger()] == ["usage"] and isinstance(m.audit_log(), list)
    m.revoke_consent(r["id"])
    with pytest.raises(VocalFaceError):
        VocalFace(api_key="bad", http_client=client).usage()
