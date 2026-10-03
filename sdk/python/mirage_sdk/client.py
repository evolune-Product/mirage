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

    # creative: photo avatar, backgrounds, assets, creative videos
    def create_photo_replica(self, name: str, photo_url: str, idle_seconds: float = 4.0, head_motion: float = 1.0) -> dict:
        """Replica from ONE portrait photo (closed mouth, open eyes). Needs consent like any replica; animation takes minutes once."""
        return self._req("POST", "/replicas/photo", json={"name": name, "photo_url": photo_url, "idle_seconds": idle_seconds, "head_motion": head_motion})
    def get_photo_replica(self, rid: str) -> dict: return self._req("GET", f"/replicas/{rid}/photo")
    def set_background(self, rid: str, **spec) -> dict:
        """e.g. set_background(rid, type="color", color="#101828") | type="gradient", colors=[..] | type="image", asset_id=.. | type="blur"."""
        return self._req("POST", f"/replicas/{rid}/background", json=spec)
    def get_background(self, rid: str) -> dict: return self._req("GET", f"/replicas/{rid}/background")
    def delete_background(self, rid: str) -> dict: return self._req("DELETE", f"/replicas/{rid}/background")
    def upload_asset(self, path: str, kind: str = "background") -> dict:
        with open(path, "rb") as f:
            return self._req("POST", "/creative/assets", files={"file": (path.split("/")[-1], f)}, data={"kind": kind})
    def list_assets(self) -> list: return self._req("GET", "/creative/assets")
    def delete_asset(self, asset_id: str) -> dict: return self._req("DELETE", f"/creative/assets/{asset_id}")
    def creative_options(self) -> dict: return self._req("GET", "/creative/options")
    def preview_scenes(self, script: str) -> dict: return self._req("POST", "/videos/scenes/preview", json={"script": script})
    def render_video(self, replica_id: str, script: str, **options) -> dict:
        """Creative video: format ("16:9"|"9:16"|"1:1"), resolution, background, captions={"style":..}, logo={"asset_id":..},
        transition, scenes, voice, callback_url. Blank-line separated paragraphs become scenes."""
        return self._req("POST", "/video-jobs/render", json={"replica_id": replica_id, "script": script, **options})
    def video_creative(self, vid: str) -> dict:
        """Options, progress {stage, scene, scenes, percent}, thumbnail_url, captions_url of a creative video."""
        return self._req("GET", f"/videos/{vid}/creative")

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
                       llm: str = "ollama/llama3.2:3b", tts_voice: str = "default", knowledge: str = "") -> dict:
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
    def create_conversation(self, persona_id: str, **options) -> dict:
        """options: participant_id, context, variables={...}, max_seconds, language"""
        return self._req("POST", "/conversations", json={"persona_id": persona_id, **options})
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

    # ---- transcripts, summaries, objectives, tools log, metrics ----
    def get_transcript(self, cid: str) -> dict: return self._req("GET", f"/conversations/{cid}/transcript")
    def get_transcript_text(self, cid: str) -> str:
        r = self._http.get(f"/v1/conversations/{cid}/transcript", params={"format": "text"}, headers={"x-api-key": self.api_key or ""})
        r.raise_for_status()
        return r.text
    def get_summary(self, cid: str) -> dict: return self._req("GET", f"/conversations/{cid}/summary")
    def get_objectives(self, cid: str) -> list: return self._req("GET", f"/conversations/{cid}/objectives")
    def get_tool_calls(self, cid: str) -> list: return self._req("GET", f"/conversations/{cid}/tool-calls")
    def get_conversation_metrics(self, cid: str) -> dict: return self._req("GET", f"/conversations/{cid}/metrics")

    # ---- persona config / tools ----
    def get_persona_config(self, pid: str) -> dict: return self._req("GET", f"/personas/{pid}/config")
    def set_persona_config(self, pid: str, **fields) -> dict:
        """language, greeting, objectives=[{name, description, success_criteria, output_variables}],
        guardrails=[{rule, forbidden_phrases}], guardrail_fallback, memory_enabled,
        custom_llm={base_url, model, api_key}, stt_model. Only the given fields change."""
        return self._req("PUT", f"/personas/{pid}/config", json=fields)
    def add_tool(self, pid: str, name: str, webhook_url: str, description: str = "", parameters: Optional[dict] = None,
                 secret: Optional[str] = None, timeout_s: float = 8.0) -> dict:
        return self._req("POST", f"/personas/{pid}/tools", json={
            "name": name, "description": description, "webhook_url": webhook_url, "secret": secret, "timeout_s": timeout_s,
            "parameters": parameters or {"type": "object", "properties": {}}})
    def list_tools(self, pid: str) -> list: return self._req("GET", f"/personas/{pid}/tools")
    def delete_tool(self, pid: str, tool_id: str) -> Any: return self._req("DELETE", f"/personas/{pid}/tools/{tool_id}")

    # ---- webhooks ----
    def webhook_events(self) -> dict: return self._req("GET", "/webhooks/events")
    def create_webhook(self, url: str, events: Optional[list] = None, description: str = "") -> dict:
        """Returns the signing `secret` once; store it to verify deliveries with `verify_webhook`."""
        return self._req("POST", "/webhooks", json={"url": url, "events": events or ["*"], "description": description})
    def list_webhooks(self) -> list: return self._req("GET", "/webhooks")
    def update_webhook(self, wid: str, **fields) -> dict: return self._req("PATCH", f"/webhooks/{wid}", json=fields)
    def delete_webhook(self, wid: str) -> Any: return self._req("DELETE", f"/webhooks/{wid}")
    def rotate_webhook_secret(self, wid: str) -> dict: return self._req("POST", f"/webhooks/{wid}/rotate-secret")
    def test_webhook(self, wid: str) -> dict: return self._req("POST", f"/webhooks/{wid}/test")
    def webhook_deliveries(self, wid: str, limit: int = 50) -> list:
        return self._req("GET", f"/webhooks/{wid}/deliveries", params={"limit": limit})
    def retry_webhook_delivery(self, delivery_id: str) -> dict: return self._req("POST", f"/webhooks/deliveries/{delivery_id}/retry")

    @staticmethod
    def verify_webhook(secret: str, body: str, signature_header: str, tolerance_s: int = 300) -> bool:
        """Verify a `Mirage-Signature` header (t=<unix>,v1=<hmac_sha256(secret, "t.body")>) against the raw body."""
        import hashlib, hmac, time
        try:
            parts = dict(p.split("=", 1) for p in signature_header.split(","))
            expected = hmac.new(secret.encode(), f"{int(parts['t'])}.{body}".encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, parts["v1"]) and abs(time.time() - int(parts["t"])) <= tolerance_s
        except Exception:
            return False

    # ---- voices / languages ----
    def voices(self, language: Optional[str] = None) -> dict:
        return self._req("GET", "/voices", params={"language": language} if language else None)
    def languages(self) -> list: return self._req("GET", "/languages")

    # ---- cloned voice (consent-gated: needs an active, voice-verified consent record for the replica) ----
    def create_voice(self, replica_id: str, force: bool = False) -> dict:
        """Start cloning the replica's voice (async). Poll get_voice() until status == 'ready'. 403 without consent."""
        return self._req("POST", f"/replicas/{replica_id}/voice", json={"force": force})
    def get_voice(self, replica_id: str) -> dict:
        """status none|queued|processing|ready|failed|revoked, similarity, wer, synth_rtf, usable_in_conversations."""
        return self._req("GET", f"/replicas/{replica_id}/voice")
    def delete_voice(self, replica_id: str) -> dict: return self._req("DELETE", f"/replicas/{replica_id}/voice")
    def preview_voice(self, replica_id: str, text: str, language: str = "en") -> bytes:
        """WAV bytes of the cloned voice saying `text`."""
        r = self._http.post(f"/v1/replicas/{replica_id}/voice/preview", headers={"x-api-key": self.api_key},
                            json={"text": text, "language": language}, timeout=300)
        if r.status_code >= 400:
            raise MirageError(r.status_code, r.text)
        return r.content

    # ---- API keys ----
    def create_key(self, name: str = "key") -> dict: return self._req("POST", "/keys", json={"name": name})
    def list_keys(self) -> list: return self._req("GET", "/keys")
    def revoke_key(self, key_id: str) -> dict: return self._req("DELETE", f"/keys/{key_id}")
    def rotate_signup_key(self) -> dict:
        out = self._req("POST", "/keys/legacy/rotate")
        self.api_key = out["key"]
        return out

    # ---- analytics ----
    def analytics(self, days: int = 30) -> dict: return self._req("GET", "/analytics", params={"days": days})

    # ---- video features ----
    def preview_template(self, script_template: str, variables: Optional[dict] = None) -> dict:
        return self._req("POST", "/videos/template/preview", json={"script_template": script_template, "variables": variables or {}})
    def create_bulk_videos(self, replica_id: str, script_template: str, rows: list, voice: str = "default",
                           callback_url: Optional[str] = None) -> dict:
        return self._req("POST", "/video-jobs/bulk", json={"replica_id": replica_id, "script_template": script_template,
                         "rows": rows, "voice": voice, "callback_url": callback_url})
    def translate_video(self, replica_id: str, script: str, languages: list, source_language: str = "en",
                        include_original: bool = False, voices: Optional[dict] = None, callback_url: Optional[str] = None) -> dict:
        return self._req("POST", "/video-jobs/translate", json={
            "replica_id": replica_id, "script": script, "languages": languages, "source_language": source_language,
            "include_original": include_original, "voices": voices or {}, "callback_url": callback_url}, timeout=300)
    def list_video_batches(self) -> list: return self._req("GET", "/video-batches")
    def get_video_batch(self, bid: str) -> dict: return self._req("GET", f"/video-batches/{bid}")

    # ---- guest share links ----
    def create_share_link(self, persona_id: str, **opts) -> dict:
        """opts: label, max_seconds, max_total_seconds, max_sessions_per_hour, max_sessions_per_ip_hour, expires_in_hours"""
        return self._req("POST", f"/personas/{persona_id}/share", json=opts)
    def list_share_links(self, persona_id: str) -> list: return self._req("GET", f"/personas/{persona_id}/share")
    def revoke_share_link(self, token: str) -> dict: return self._req("DELETE", f"/share/{token}")

    # templates, leads, widget, integrations
    def list_templates(self, niche: Optional[str] = None) -> dict:
        return self._req("GET", "/templates", params={"niche": niche} if niche else None)
    def get_template(self, template_id: str) -> dict: return self._req("GET", f"/templates/{template_id}")
    def instantiate_template(self, template_id: str, **opts) -> dict:
        """opts: name, variables, language, llm, tts_voice, replica_id, include_sample_knowledge, enable_lead_capture,
        booking_webhook_url, booking_secret, notify_webhook_url, notify_secret"""
        return self._req("POST", f"/templates/{template_id}/instantiate", json=opts)
    def list_leads(self, **filters) -> dict:
        """filters: persona_id, conversation_id, since, until, q, consent, limit, offset"""
        return self._req("GET", "/leads", params={k: v for k, v in filters.items() if v is not None})
    def get_lead(self, lead_id: str) -> dict: return self._req("GET", f"/leads/{lead_id}")
    def delete_lead(self, lead_id: str) -> dict: return self._req("DELETE", f"/leads/{lead_id}")
    def create_lead(self, persona_id: str, **fields) -> dict:
        return self._req("POST", "/leads", json={"persona_id": persona_id, **fields})
    def export_leads_csv(self, **filters) -> str:
        r = self._http.get("/v1/leads/export.csv", headers={"x-api-key": self.api_key or ""},
                           params={k: v for k, v in filters.items() if v is not None})
        if r.status_code >= 400:
            raise MirageError(r.status_code, r.text)
        return r.text
    def get_lead_capture(self, persona_id: str) -> dict: return self._req("GET", f"/personas/{persona_id}/lead-capture")
    def set_lead_capture(self, persona_id: str, **cfg) -> dict:
        """cfg: enabled, required_fields, require_consent, disclosure"""
        return self._req("PUT", f"/personas/{persona_id}/lead-capture", json=cfg)
    def create_widget(self, persona_id: str, **opts) -> dict:
        """opts: allowed_domains, label, color, position, greeting, language, max_seconds, ... -> includes the copy-paste `snippet`"""
        return self._req("POST", f"/personas/{persona_id}/widget", json=opts)
    def list_widgets(self, persona_id: Optional[str] = None) -> list:
        return self._req("GET", "/widgets", params={"persona_id": persona_id} if persona_id else None)
    def update_widget(self, token: str, **fields) -> dict: return self._req("PUT", f"/widgets/{token}", json=fields)
    def delete_widget(self, token: str) -> dict: return self._req("DELETE", f"/widgets/{token}")
    def get_integrations(self, persona_id: str) -> dict: return self._req("GET", f"/personas/{persona_id}/integrations")
    def set_integrations(self, persona_id: str, **cfg) -> dict:
        """cfg: booking_webhook_url, booking_secret, notify_webhook_url, notify_secret"""
        return self._req("PUT", f"/personas/{persona_id}/integrations", json=cfg)
    def test_integration(self, persona_id: str, which: str = "booking") -> dict:
        return self._req("POST", f"/personas/{persona_id}/integrations/test", json={"which": which})
