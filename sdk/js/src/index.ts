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

  createConversation(persona_id: string) { return this.req<Conversation>("POST", "/conversations", { persona_id }); }
  endConversation(cid: string) { return this.req<Conversation>("POST", `/conversations/${cid}/end`); }

  createVideo(replica_id: string, script: string) { return this.req<Video>("POST", "/videos", { replica_id, script }); }
  getVideo(vid: string) { return this.req<Video>("GET", `/videos/${vid}`); }

  billingPlans() { return this.req("GET", "/billing/plans", undefined, false); }
  billingStatus() { return this.req("GET", "/billing/status"); }
  checkout(provider: "stripe" | "razorpay", sku: string, kind: "topup" | "plan" = "topup", urls: { success_url?: string; cancel_url?: string } = {}) {
    return this.req<{ provider: string; checkout_url: string; id: string }>("POST", "/billing/checkout", { provider, kind, sku, ...urls });
  }
  moderate(text: string) { return this.req<{ allowed: boolean; reasons: string[]; classifier: string }>("POST", "/moderation/check", { text }); }
}
