import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import db, knowledge as kb
from app.main import app


class FakeEmbedder:
    """Bag-of-keywords embedder: deterministic, no model."""
    name = "fake:v1"
    VOCAB = ["price", "refund", "shipping", "cat", "dog", "hours"]

    def embed(self, texts):
        v = np.array([[t.lower().count(w) for w in self.VOCAB] for t in texts], dtype=np.float32) + 1e-3
        return v / np.linalg.norm(v, axis=1, keepdims=True)


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


def setup(c, email="a@b.com"):
    h = {"x-api-key": c.post("/v1/signup", json={"email": email}).json()["api_key"]}
    pid = c.post("/v1/personas", json={"name": "p", "system_prompt": "x"}, headers=h).json()["id"]
    return h, pid


def test_chunking():
    assert kb.chunk_text("") == []
    long = "word. " * 500
    chunks = kb.chunk_text(long, max_chars=300, overlap=20)
    assert len(chunks) > 5 and all(len(c) <= 340 for c in chunks)
    assert len(kb.chunk_text("a\n\nb")) == 1


def test_bm25_ranking():
    s = kb.bm25_scores("refund policy", ["our refund policy is 30 days", "we ship worldwide", "cats are great"])
    assert s[0] > s[1] and s[0] > s[2]


def test_dense_retrieve_and_isolation(client):
    h, pid = setup(client)
    client.post(f"/v1/personas/{pid}/knowledge/text", json={"title": "faq", "text": "Refund within 30 days.\n\nShipping takes 3 days."}, headers=h)
    client.post(f"/v1/personas/{pid}/knowledge/text", json={"title": "pets", "text": "Our cat and dog policy.\n\n" + "cat " * 40 + "\n\n" + "dog " * 5}, headers=h)
    # chunking merges short paragraphs; force separate docs for clearer ranking
    hits = kb.retrieve(pid, "what is the refund?", 2)
    assert hits and hits[0]["method"] == "dense" and "Refund" in hits[0]["text"]
    assert kb.retrieve("p_other", "refund") == []
    assert "Relevant knowledge" in kb.format_context(hits)


def test_bm25_fallback_when_no_embedder(client):
    h, pid = setup(client)
    kb.set_embedder(None)
    kb._embedder_failed = True  # simulate fastembed unavailable
    client.post(f"/v1/personas/{pid}/knowledge/text", json={"title": "a", "text": "Opening hours are nine to five."}, headers=h)
    client.post(f"/v1/personas/{pid}/knowledge/text", json={"title": "b", "text": "Unrelated gardening notes."}, headers=h)
    hits = kb.retrieve(pid, "opening hours", 1)
    assert hits[0]["method"] == "bm25" and "hours" in hits[0]["text"]


def test_upload_list_delete_and_ownership(client):
    h, pid = setup(client)
    r = client.post(f"/v1/personas/{pid}/knowledge/upload", files={"file": ("notes.txt", b"Shipping is free over $50.")}, headers=h)
    assert r.status_code == 200 and r.json()["n_chunks"] == 1
    did = r.json()["id"]
    assert len(client.get(f"/v1/personas/{pid}/knowledge", headers=h).json()) == 1
    h2, _ = setup(client, "c@d.com")
    assert client.get(f"/v1/personas/{pid}/knowledge", headers=h2).status_code == 404
    assert client.delete(f"/v1/personas/{pid}/knowledge/{did}", headers=h).status_code == 200
    assert client.get(f"/v1/personas/{pid}/knowledge", headers=h).json() == []
    assert kb.retrieve(pid, "shipping") == []
    assert client.post(f"/v1/personas/{pid}/knowledge/text", json={"title": "e", "text": "  "}, headers=h).status_code == 422


def test_pdf_upload(client):
    from pypdf import PdfWriter
    import io
    h, pid = setup(client)
    w = PdfWriter(); w.add_blank_page(200, 200); b = io.BytesIO(); w.write(b)
    # blank PDF has no text -> 422, proving the PDF path is taken
    r = client.post(f"/v1/personas/{pid}/knowledge/upload", files={"file": ("x.pdf", b.getvalue())}, headers=h)
    assert r.status_code == 422


def test_memory(client):
    h, pid = setup(client)
    r = client.post(f"/v1/personas/{pid}/memories", json={"turns": [{"role": "user", "content": "I am Sam"}, {"role": "assistant", "content": "hi"}]}, headers=h)
    assert "Sam" in r.json()["summary"]
    client.post(f"/v1/personas/{pid}/memories", json={"summary": "likes cats"}, headers=h)
    mem = client.get(f"/v1/personas/{pid}/memories", headers=h).json()
    assert len(mem) == 2 and mem[0]["summary"] == "likes cats"
    assert kb.summarize_turns([{"role": "user", "content": "x"}], llm=lambda p: "LLM summary") == "LLM summary"
    assert kb.summarize_turns([{"role": "user", "content": "x"}], llm=lambda p: 1 / 0).startswith("User said")
