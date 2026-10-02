"""Synchronous httpx client for every /v1 endpoint of the Mirage API."""
from typing import Any, Optional

import httpx


class MirageError(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(f"HTTP {status}: {detail}")
        self.status, self.detail = status, detail


class Mirage:
    def __init__(self, api_key: Optional[str] = None, base_url: str = "http://localhost:8000",
                 http_client: Optional[httpx.Client] = None, timeout: float = 30.0):
        self.api_key = api_key
        self._http = http_client or httpx.Client(base_url=base_url, timeout=timeout)

    def _req(self, method: str, path: str, auth: bool = True, **kw) -> Any:
        headers = {"x-api-key": self.api_key} if (auth and self.api_key) else {}
        r = self._http.request(method, "/v1" + path, headers=headers, **kw)
        if r.status_code >= 400:
            try:
                d = r.json().get("detail", r.text)
            except Exception:
                d = r.text
            raise MirageError(r.status_code, d)
        return r.json() if r.content else None

    # account
    def signup(self, email: str) -> dict:
        out = self._req("POST", "/signup", auth=False, json={"email": email})
        self.api_key = out["api_key"]
        return out

    def usage(self) -> dict: return self._req("GET", "/usage")
    def usage_ledger(self, limit: int = 100) -> list: return self._req("GET", "/usage/ledger", params={"limit": limit})
    def audit_log(self, limit: int = 100) -> list: return self._req("GET", "/audit", params={"limit": limit})

    # replicas
    def create_replica(self, name: str, train_video_url: str) -> dict:
        return self._req("POST", "/replicas", json={"name": name, "train_video_url": train_video_url})
    def list_replicas(self) -> list: return self._req("GET", "/replicas")
    def get_replica(self, rid: str) -> dict: return self._req("GET", f"/replicas/{rid}")

    # consent
    def consent_challenge(self, rid: str) -> dict: return self._req("POST", f"/replicas/{rid}/consent/challenge")
    def record_consent(self, rid: str, challenge_id: str, speaker_name: str, audio_url: str, transcript: str) -> dict:
        return self._req("POST", f"/replicas/{rid}/consent", json={
            "challenge_id": challenge_id, "speaker_name": speaker_name,
            "audio_url": audio_url, "transcript": transcript})
    def get_consent(self, rid: str) -> dict: return self._req("GET", f"/replicas/{rid}/consent")
    def revoke_consent(self, rid: str) -> dict: return self._req("DELETE", f"/replicas/{rid}/consent")

    # personas
    def create_persona(self, name: str, system_prompt: str, replica_id: Optional[str] = None,
                       llm: str = "ollama/llama3.2", tts_voice: str = "default", knowledge: str = "") -> dict:
        return self._req("POST", "/personas", json={"name": name, "system_prompt": system_prompt,
                         "replica_id": replica_id, "llm": llm, "tts_voice": tts_voice, "knowledge": knowledge})
    def list_personas(self) -> list: return self._req("GET", "/personas")

    # knowledge base
    def add_knowledge_text(self, pid: str, title: str, text: str) -> dict:
        return self._req("POST", f"/personas/{pid}/knowledge/text", json={"title": title, "text": text})
    def upload_knowledge(self, pid: str, filename: str, content: bytes, title: Optional[str] = None) -> dict:
        return self._req("POST", f"/personas/{pid}/knowledge/upload", files={"file": (filename, content)},
                         data={"title": title} if title else None)
    def list_knowledge(self, pid: str) -> list: return self._req("GET", f"/personas/{pid}/knowledge")
    def delete_knowledge(self, pid: str, did: str) -> Any: return self._req("DELETE", f"/personas/{pid}/knowledge/{did}")
    def search_knowledge(self, pid: str, query: str, k: int = 4) -> Any:
        return self._req("POST", f"/personas/{pid}/knowledge/search", json={"query": query, "k": k})

    # memories
    def add_memory(self, pid: str, **fields) -> dict: return self._req("POST", f"/personas/{pid}/memories", json=fields)
    def list_memories(self, pid: str, limit: int = 50) -> list:
        return self._req("GET", f"/personas/{pid}/memories", params={"limit": limit})

    # conversations
    def create_conversation(self, persona_id: str) -> dict:
        return self._req("POST", "/conversations", json={"persona_id": persona_id})
    def end_conversation(self, cid: str) -> dict: return self._req("POST", f"/conversations/{cid}/end")

    # videos
    def create_video(self, replica_id: str, script: str) -> dict:
        return self._req("POST", "/videos", json={"replica_id": replica_id, "script": script})
    def get_video(self, vid: str) -> dict: return self._req("GET", f"/videos/{vid}")

    # billing / safety
    def billing_plans(self) -> dict: return self._req("GET", "/billing/plans", auth=False)
    def billing_status(self) -> dict: return self._req("GET", "/billing/status")
    def checkout(self, provider: str, sku: str, kind: str = "topup", **urls) -> dict:
        return self._req("POST", "/billing/checkout", json={"provider": provider, "kind": kind, "sku": sku, **urls})
    def moderate(self, text: str) -> dict: return self._req("POST", "/moderation/check", json={"text": text})
