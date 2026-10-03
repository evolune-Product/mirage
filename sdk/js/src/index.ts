export class MirageError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(`HTTP ${status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  }
}

export interface Replica { id: string; account_id: string; name: string; train_video_url: string; status: string; created_at: string }
export interface Persona { id: string; name: string; system_prompt: string; replica_id?: string | null; llm: string; tts_voice: string; knowledge: string }
export interface Conversation { id: string; persona_id: string; status: string; room_url: string; seconds_used: number }
export interface Video { id: string; replica_id: string; script: string; status: string; output_url?: string | null }
export interface LedgerEntry { id: string; kind: string; seconds: number; amount_cents: number; ref?: string | null; note: string; created_at: string }
export interface ConsentChallenge { challenge_id: string; phrase: string; expires_at: string }

export interface TranscriptTurn { seq: number; role: "user" | "assistant"; text: string; t_ms: number; first_audio_ms: number | null; interrupted: boolean }
export interface ObjectiveResult { name: string; completed: boolean; evidence: string; variables: Record<string, string> }
export interface PersonaConfigInput {
  language?: string; greeting?: string; memory_enabled?: boolean; guardrail_fallback?: string; stt_model?: string;
  objectives?: { name: string; description?: string; success_criteria?: string; output_variables?: string[] }[];
  guardrails?: { name?: string; rule: string; forbidden_phrases?: string[] }[];
  custom_llm?: { base_url: string; model?: string; api_key?: string };
}
export interface PersonaConfig extends Omit<PersonaConfigInput, "custom_llm"> { persona_id: string; custom_llm: { base_url: string; model: string; has_api_key: boolean } }
export interface VideoBatch { id: string; kind: string; total: number; counts: Record<string, number>; completed: boolean; items?: { video_id: string; status: string; language: string; script: string; output_url: string | null }[] }

/** Verify a `Mirage-Signature` header against the raw request body (Node 18+/browsers with WebCrypto). */
export async function verifyWebhook(secret: string, body: string, header: string, toleranceS = 300): Promise<boolean> {
  try {
    const parts = Object.fromEntries(header.split(",").map((p) => p.split("=", 2) as [string, string]));
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const sig = await crypto.subtle.sign("HMAC", key, enc.encode(`${parts.t}.${body}`));
    const hex = [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
    return hex === parts.v1 && Math.abs(Date.now() / 1000 - Number(parts.t)) <= toleranceS;
  } catch { return false; }
}

export interface MirageOptions { apiKey?: string; baseUrl?: string; fetch?: typeof fetch }

export class Mirage {
  apiKey?: string;
  private base: string;
  private f: typeof fetch;

  constructor(opts: MirageOptions = {}) {
    this.apiKey = opts.apiKey;
    this.base = (opts.baseUrl ?? "http://localhost:8000").replace(/\/$/, "");
    this.f = opts.fetch ?? ((...a) => fetch(...a));
  }

  private async req<T = any>(method: string, path: string, body?: unknown, auth = true, query?: Record<string, string | number>): Promise<T> {
    const headers: Record<string, string> = {};
    if (auth && this.apiKey) headers["x-api-key"] = this.apiKey;
    let init: RequestInit = { method, headers };
    if (body instanceof FormData) init.body = body;
    else if (body !== undefined) { headers["content-type"] = "application/json"; init.body = JSON.stringify(body); }
    const qs = query ? "?" + new URLSearchParams(Object.entries(query).map(([k, v]) => [k, String(v)])) : "";
    const r = await this.f(`${this.base}/v1${path}${qs}`, init);
    const text = await r.text();
    const data = text ? JSON.parse(text) : null;
    if (!r.ok) throw new MirageError(r.status, data?.detail ?? text);
    return data as T;
  }

  async signup(email: string) {
    const out = await this.req<{ account_id: string; api_key: string; credits_seconds: number }>("POST", "/signup", { email }, false);
    this.apiKey = out.api_key;
    return out;
  }
  usage() { return this.req<{ credits_seconds: number }>("GET", "/usage"); }
  usageLedger(limit = 100) { return this.req<LedgerEntry[]>("GET", "/usage/ledger", undefined, true, { limit }); }
  auditLog(limit = 100) { return this.req<any[]>("GET", "/audit", undefined, true, { limit }); }

  createReplica(name: string, train_video_url: string) { return this.req<Replica>("POST", "/replicas", { name, train_video_url }); }
  listReplicas() { return this.req<Replica[]>("GET", "/replicas"); }
  getReplica(id: string) { return this.req<Replica>("GET", `/replicas/${id}`); }

  consentChallenge(rid: string) { return this.req<ConsentChallenge>("POST", `/replicas/${rid}/consent/challenge`); }
  recordConsent(rid: string, b: { challenge_id: string; speaker_name: string; audio_url: string; transcript: string }) { return this.req("POST", `/replicas/${rid}/consent`, b); }
  getConsent(rid: string) { return this.req<{ replica_id: string; has_consent: boolean; records: any[] }>("GET", `/replicas/${rid}/consent`); }
  revokeConsent(rid: string) { return this.req<{ revoked: number }>("DELETE", `/replicas/${rid}/consent`); }

  createPersona(p: { name: string; system_prompt: string; replica_id?: string | null; llm?: string; tts_voice?: string; knowledge?: string }) { return this.req<Persona>("POST", "/personas", p); }
  listPersonas() { return this.req<Persona[]>("GET", "/personas"); }

  addKnowledgeText(pid: string, title: string, text: string) { return this.req("POST", `/personas/${pid}/knowledge/text`, { title, text }); }
  uploadKnowledge(pid: string, file: Blob, filename: string, title?: string) {
    const fd = new FormData(); fd.append("file", file, filename); if (title) fd.append("title", title);
    return this.req("POST", `/personas/${pid}/knowledge/upload`, fd);
  }
  listKnowledge(pid: string) { return this.req<any[]>("GET", `/personas/${pid}/knowledge`); }
  deleteKnowledge(pid: string, did: string) { return this.req("DELETE", `/personas/${pid}/knowledge/${did}`); }
  searchKnowledge(pid: string, query: string, k = 4) { return this.req("POST", `/personas/${pid}/knowledge/search`, { query, k }); }

  addMemory(pid: string, m: Record<string, unknown>) { return this.req("POST", `/personas/${pid}/memories`, m); }
  listMemories(pid: string, limit = 50) { return this.req<any[]>("GET", `/personas/${pid}/memories`, undefined, true, { limit }); }

  createConversation(persona_id: string, options: { participant_id?: string; context?: string; variables?: Record<string, string>; max_seconds?: number; language?: string } = {}) {
    return this.req<Conversation>("POST", "/conversations", { persona_id, ...options });
  }
  endConversation(cid: string) { return this.req<Conversation>("POST", `/conversations/${cid}/end`); }

  createVideo(replica_id: string, script: string) { return this.req<Video>("POST", "/videos", { replica_id, script }); }
  getVideo(vid: string) { return this.req<Video>("GET", `/videos/${vid}`); }

  billingPlans() { return this.req("GET", "/billing/plans", undefined, false); }
  billingStatus() { return this.req("GET", "/billing/status"); }
  checkout(provider: "stripe" | "razorpay", sku: string, kind: "topup" | "plan" = "topup", urls: { success_url?: string; cancel_url?: string } = {}) {
    return this.req<{ provider: string; checkout_url: string; id: string }>("POST", "/billing/checkout", { provider, kind, sku, ...urls });
  }
  moderate(text: string) { return this.req<{ allowed: boolean; reasons: string[]; classifier: string }>("POST", "/moderation/check", { text }); }
  // ---- transcripts, summaries, objectives, tool log, metrics ----
  getTranscript(cid: string) { return this.req<{ conversation_id: string; summary: string; turns: TranscriptTurn[] }>("GET", `/conversations/${cid}/transcript`); }
  getSummary(cid: string) { return this.req<{ conversation_id: string; summary: string; ready: boolean }>("GET", `/conversations/${cid}/summary`); }
  getObjectives(cid: string) { return this.req<ObjectiveResult[]>("GET", `/conversations/${cid}/objectives`); }
  getToolCalls(cid: string) { return this.req<any[]>("GET", `/conversations/${cid}/tool-calls`); }
  getConversationMetrics(cid: string) { return this.req<any>("GET", `/conversations/${cid}/metrics`); }

  // ---- persona config / tools ----
  getPersonaConfig(pid: string) { return this.req<PersonaConfig>("GET", `/personas/${pid}/config`); }
  setPersonaConfig(pid: string, cfg: PersonaConfigInput) { return this.req<PersonaConfig>("PUT", `/personas/${pid}/config`, cfg); }
  addTool(pid: string, t: { name: string; webhook_url: string; description?: string; parameters?: object; secret?: string; timeout_s?: number }) { return this.req("POST", `/personas/${pid}/tools`, t); }
  listTools(pid: string) { return this.req<any[]>("GET", `/personas/${pid}/tools`); }
  deleteTool(pid: string, id: string) { return this.req("DELETE", `/personas/${pid}/tools/${id}`); }

  // ---- webhooks ----
  webhookEvents() { return this.req<{ events: string[] }>("GET", "/webhooks/events"); }
  createWebhook(url: string, events: string[] = ["*"], description = "") { return this.req<{ id: string; secret: string; url: string; events: string[] }>("POST", "/webhooks", { url, events, description }); }
  listWebhooks() { return this.req<any[]>("GET", "/webhooks"); }
  updateWebhook(id: string, patch: { url?: string; events?: string[]; active?: boolean; description?: string }) { return this.req("PATCH", `/webhooks/${id}`, patch); }
  deleteWebhook(id: string) { return this.req("DELETE", `/webhooks/${id}`); }
  rotateWebhookSecret(id: string) { return this.req<{ secret: string }>("POST", `/webhooks/${id}/rotate-secret`); }
  testWebhook(id: string) { return this.req("POST", `/webhooks/${id}/test`); }
  webhookDeliveries(id: string, limit = 50) { return this.req<any[]>("GET", `/webhooks/${id}/deliveries`, undefined, true, { limit }); }
  retryWebhookDelivery(deliveryId: string) { return this.req("POST", `/webhooks/deliveries/${deliveryId}/retry`); }

  // ---- voices / languages ----
  voices(language?: string) { return this.req<{ languages: any[]; voices: any[] }>("GET", "/voices", undefined, true, language ? { language } : undefined); }
  // ---- cloned voice (consent-gated: needs an active, voice-verified consent record for the replica) ----
  createVoice(replica_id: string, force = false) { return this.req<ReplicaVoice>("POST", `/replicas/${replica_id}/voice`, { force }); }
  getVoice(replica_id: string) { return this.req<ReplicaVoice>("GET", `/replicas/${replica_id}/voice`); }
  deleteVoice(replica_id: string) { return this.req<{ deleted: boolean }>("DELETE", `/replicas/${replica_id}/voice`); }

  // ---- API keys ----
  createKey(name = "key") { return this.req<{ id: string; key: string; prefix: string }>("POST", "/keys", { name }); }
  listKeys() { return this.req<any[]>("GET", "/keys"); }
  revokeKey(id: string) { return this.req("DELETE", `/keys/${id}`); }

  // ---- analytics ----
  analytics(days = 30) { return this.req<any>("GET", "/analytics", undefined, true, { days }); }

  // ---- creative: photo avatar, backgrounds, assets, creative videos ----
  createPhotoReplica(name: string, photo_url: string, opts: { idle_seconds?: number; head_motion?: number } = {}) { return this.req<Replica>("POST", "/replicas/photo", { name, photo_url, ...opts }); }
  getPhotoReplica(id: string) { return this.req<any>("GET", `/replicas/${id}/photo`); }
  setBackground(id: string, spec: { type: "color" | "gradient" | "image" | "blur" | "none"; color?: string; colors?: string[]; angle?: number; asset_id?: string; blur?: number; radius?: number }) { return this.req<any>("POST", `/replicas/${id}/background`, spec); }
  getBackground(id: string) { return this.req<any>("GET", `/replicas/${id}/background`); }
  deleteBackground(id: string) { return this.req<any>("DELETE", `/replicas/${id}/background`); }
  uploadAsset(file: Blob, kind: "background" | "logo" = "background", filename = "asset.png") { const f = new FormData(); f.append("file", file, filename); f.append("kind", kind); return this.req<any>("POST", "/creative/assets", f); }
  listAssets() { return this.req<any[]>("GET", "/creative/assets"); }
  creativeOptions() { return this.req<any>("GET", "/creative/options"); }
  previewScenes(script: string) { return this.req<any>("POST", "/videos/scenes/preview", { script }); }
  renderVideo(b: { replica_id: string; script: string; voice?: string; callback_url?: string; format?: "16:9" | "9:16" | "1:1"; resolution?: 480 | 720 | 1080; background?: any; captions?: { style: "classic" | "bold" | "minimal" | "karaoke"; accent?: string }; logo?: { asset_id: string; position?: string; scale?: number; opacity?: number }; transition?: "cut" | "fade" | "dip" | "slide"; transition_s?: number; scenes?: "paragraphs" | "single"; thumbnail?: boolean }) { return this.req<any>("POST", "/video-jobs/render", b); }
  getVideoCreative(id: string) { return this.req<any>("GET", `/videos/${id}/creative`); }

  // ---- video features ----
  previewTemplate(script_template: string, variables: Record<string, string> = {}) { return this.req<{ variables: string[]; missing: string[]; rendered: string | null }>("POST", "/videos/template/preview", { script_template, variables }); }
  createBulkVideos(b: { replica_id: string; script_template: string; rows: Record<string, string>[]; voice?: string; callback_url?: string }) { return this.req<VideoBatch>("POST", "/video-jobs/bulk", b); }
  translateVideo(b: { replica_id: string; script: string; languages: string[]; source_language?: string; include_original?: boolean; voices?: Record<string, string>; callback_url?: string }) { return this.req<VideoBatch>("POST", "/video-jobs/translate", b); }
  listVideoBatches() { return this.req<VideoBatch[]>("GET", "/video-batches"); }
  getVideoBatch(id: string) { return this.req<VideoBatch>("GET", `/video-batches/${id}`); }

  // ---- guest share links ----
  createShareLink(persona_id: string, o: { label?: string; max_seconds?: number; max_total_seconds?: number; max_sessions_per_hour?: number; max_sessions_per_ip_hour?: number; expires_in_hours?: number } = {}) { return this.req<{ token: string; url: string }>("POST", `/personas/${persona_id}/share`, o); }
  listShareLinks(persona_id: string) { return this.req<any[]>("GET", `/personas/${persona_id}/share`); }
  revokeShareLink(token: string) { return this.req("DELETE", `/share/${token}`); }
}

export interface ReplicaVoice {
  replica_id: string; voice_id: string; status: "none" | "queued" | "processing" | "ready" | "failed" | "revoked";
  cloned: true; engine?: string; similarity?: number | null; wer?: number | null; synth_rtf?: number | null;
  reference_seconds?: number; error?: string | null; usable_in_conversations?: boolean; notice?: string;
}
