export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const KEY = "mirage_api_key";
export const getKey = (): string => { try { return localStorage.getItem(KEY) || ""; } catch { return ""; } };
export const setKey = (k: string) => { try { localStorage.setItem(KEY, k); } catch {} };
export const clearKey = () => { try { localStorage.removeItem(KEY); } catch {} };

/** Active team workspace (sent as the X-Workspace header: requests then run as the workspace owner, limited by your role). */
const WS = "mirage_workspace";
export const getWorkspace = (): string => { try { return localStorage.getItem(WS) || ""; } catch { return ""; } };
export const setWorkspace = (id: string) => { try { id ? localStorage.setItem(WS, id) : localStorage.removeItem(WS); window.dispatchEvent(new Event("mirage:workspace")); } catch {} };
const wsHeader = (path: string): Record<string, string> => { const w = getWorkspace(); return w && !path.startsWith("/v1/workspaces") ? { "x-workspace": w } : {}; };

const detailMsg = (j: { detail?: unknown }): string => { const d = j.detail; if (typeof d === "string") return d; if (d && typeof d === "object" && typeof (d as { message?: unknown }).message === "string") return (d as { message: string }).message; return JSON.stringify(d ?? j); };
export async function api<T>(path: string, init: { method?: string; body?: unknown; key?: string } = {}): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json", ...wsHeader(path) };
  const key = init.key ?? getKey();
  if (key) headers["x-api-key"] = key;
  const res = await fetch(API_URL + path, { method: init.method || (init.body ? "POST" : "GET"), headers, body: init.body ? JSON.stringify(init.body) : undefined });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = detailMsg(j); } catch {}
    throw new Error(`${res.status}: ${msg}`);
  }
  return res.json() as Promise<T>;
}

// The backend has no list endpoints for conversations/videos, so remember ids locally.
export function loadIds(name: string): string[] { try { return JSON.parse(localStorage.getItem("mirage_" + name) || "[]"); } catch { return []; } }
export function saveId(name: string, id: string) { try { localStorage.setItem("mirage_" + name, JSON.stringify([id, ...loadIds(name).filter((x) => x !== id)].slice(0, 50))); } catch {} }

/** Multipart upload (file inputs). Do not set content-type: the browser adds the boundary. */
export async function apiForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(API_URL + path, { method: "POST", headers: { "x-api-key": getKey(), ...wsHeader(path) }, body: form });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = detailMsg(j); } catch {}
    throw new Error(`${res.status}: ${msg}`);
  }
  return res.json() as Promise<T>;
}
/** POST JSON, get a binary body back (e.g. voice preview audio/wav) as an object URL. */
export async function apiBlobUrl(path: string, body: unknown): Promise<string> {
  const res = await fetch(API_URL + path, { method: "POST", headers: { "content-type": "application/json", "x-api-key": getKey(), ...wsHeader(path) }, body: JSON.stringify(body) });
  if (!res.ok) { let msg = res.statusText; try { msg = detailMsg(await res.json()); } catch {} throw new Error(`${res.status}: ${msg}`); }
  return URL.createObjectURL(await res.blob());
}
/** Server error text without the "422: " prefix (nicer to show inline). */
export const errText = (x: unknown): string => (x instanceof Error ? x.message : String(x)).replace(/^\d{3}:\s*/, "");
/** Signed, time-limited URL for a file the API serves (thumbnails, captions, idle clips). */
export async function signedUrl(path: string): Promise<string> { const j = await api<{ url: string }>("/v1/files/sign", { body: { path } }); return j.url.startsWith("http") ? j.url : API_URL + j.url; }

export const fileUrl = (u: string) => (u.startsWith("http") ? u : API_URL + u);
export const fmtDate = (iso?: string | null) => { if (!iso) return "-"; const d = new Date(iso); return isNaN(+d) ? "-" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); };
export const fmtDur = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`);
/** Authenticated download -> object URL (for owner-only files such as consent audio, which need the x-api-key header). */
export async function fetchBlobUrl(path: string): Promise<string> {
  const res = await fetch(API_URL + path, { headers: { "x-api-key": getKey(), ...wsHeader(path) } });
  if (!res.ok) throw new Error(`${res.status}: ${res.statusText}`);
  return URL.createObjectURL(await res.blob());
}

export type Replica = { id: string; name: string; train_video_url: string; status: string; created_at: string };
export type Persona = { id: string; name: string; system_prompt: string; replica_id: string | null; llm: string; tts_voice: string; knowledge: string };
export type Conversation = { id: string; persona_id: string; status: string; room_url: string; seconds_used: number; started_at?: string; ended_at?: string | null };
export type Objective = { name: string; description: string; success_criteria: string; output_variables: string[] };
export type Guardrail = { name: string; rule: string; forbidden_phrases: string[] };
export type PersonaConfig = {
  persona_id: string; language: string; greeting: string; objectives: Objective[]; guardrails: Guardrail[]; guardrail_fallback: string;
  memory_enabled: boolean; custom_llm: { base_url: string; model: string; has_api_key: boolean }; stt_model: string;
};
export type Tool = { id: string; name: string; description: string; parameters: Record<string, unknown>; webhook_url: string; has_secret: boolean; timeout_s: number };
export type ShareLink = { token: string; url: string; persona_id: string; label: string; max_seconds: number; max_total_seconds: number; max_sessions_per_hour: number; max_sessions_per_ip_hour: number; used_seconds: number; sessions_started: number; expires_at: string | null; revoked: boolean; created_at: string };
export type Voice = { id: string; language: string; gender: string; accent: string; default_for_language: boolean; cloned?: boolean; replica_id?: string; name?: string };
export type Lang = { code: string; name: string; stt: boolean; tts: boolean; default_voice: string };
export type Turn = { seq: number; role: "user" | "assistant"; text: string; t_ms: number; first_audio_ms: number | null; interrupted: boolean; created_at: string };
export type Webhook = { id: string; url: string; events: string[]; active: boolean; description: string; created_at: string; secret?: string };
export type Delivery = { id: string; endpoint_id: string; event: string; status: string; attempts: number; last_status_code: number | null; last_error: string | null; next_attempt_at: string | null; created_at: string; delivered_at: string | null };
export type ApiKey = { id: string; name: string; prefix: string; created_at: string; last_used_at: string | null; revoked_at: string | null; legacy?: boolean };
export type Video = { id: string; replica_id: string; script: string; status: string; output_url: string | null };

export type VoiceClone = { replica_id: string; voice_id: string; status: "none" | "queued" | "processing" | "ready" | "failed" | "revoked"; engine?: string; reference_seconds?: number; reference_quality?: { snr_db?: number }; similarity?: number | null; wer?: number | null; synth_rtf?: number | null; usable_in_conversations?: boolean; error?: string | null; notice?: string };
export type PhotoStatus = { replica_id: string; replica_status: string; status: "queued" | "animating" | "ready" | "error"; error: string | null; warnings: string[]; idle_seconds: number; head_motion: number; animate_s: number | null; idle_url: string | null };
export type Background = { type: "color" | "gradient" | "image" | "blur"; color?: string; colors?: string[]; angle?: number; asset_id?: string; blur?: number; radius?: number };
export type Asset = { id: string; kind: string; filename: string; width: number; height: number; bytes: number; created_at: string };
export type Workspace = { id: string; name: string; owner_account_id: string; role: "owner" | "admin" | "member"; created_at: string };
export type Perception = { persona_id: string; enabled: boolean; consent_acknowledged: boolean; require_user_consent: boolean; camera: boolean; screen: boolean; store_frames: boolean; vlm_model: string; interval_s: number };

/** Photo replicas have an image as train_video_url; remember ids we created from a photo as well (the list endpoint has no "kind" field). */
const PH = "mirage_photo_replicas";
export const markPhotoReplica = (id: string) => { try { localStorage.setItem(PH, JSON.stringify([id, ...JSON.parse(localStorage.getItem(PH) || "[]")].slice(0, 100))); } catch {} };
export const isPhotoReplica = (r: { id: string; train_video_url: string }): boolean => {
  if (/\.(jpe?g|png|webp)(\?|#|$)/i.test(r.train_video_url || "")) return true;
  try { return (JSON.parse(localStorage.getItem(PH) || "[]") as string[]).includes(r.id); } catch { return false; }
};

export type TemplateSummary = { id: string; name: string; niche: string; summary: string; version: number; language: string; suggested_llm: string; tools: string[]; objectives: string[]; lead_fields: string[]; knowledge_docs: number; safety_notes: string };
export type TemplateDetail = TemplateSummary & { persona_name: string; system_prompt: string; greeting: string; variables: Record<string, string>; sample_questions: (string | { q: string })[]; knowledge: { title: string; chars: number }[] };
export type Instantiated = { persona_id: string; persona: { id: string; name: string; llm: string }; warnings: string[]; next_steps: string[]; knowledge_docs: { id: string; title: string }[]; lead_capture: { enabled: boolean } };
export type Lead = { id: string; conversation_id: string | null; persona_id: string; name: string; email: string; phone: string; company: string; interest: string; notes: string; consent: boolean; consent_text: string; source: string; created_at: string };
export type Widget = { token: string; persona_id: string; allowed_domains: string[]; label: string; color: string; position: string; greeting: string; language: string; script_url: string; frame_url: string; share_url: string; snippet: string; limits: { max_seconds: number; max_total_seconds: number; used_seconds: number; sessions_started: number; revoked: boolean; expires_at: string | null } };
