export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const KEY = "mirage_api_key";
export const getKey = (): string => { try { return localStorage.getItem(KEY) || ""; } catch { return ""; } };
export const setKey = (k: string) => { try { localStorage.setItem(KEY, k); } catch {} };
export const clearKey = () => { try { localStorage.removeItem(KEY); } catch {} };

export async function api<T>(path: string, init: { method?: string; body?: unknown; key?: string } = {}): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  const key = init.key ?? getKey();
  if (key) headers["x-api-key"] = key;
  const res = await fetch(API_URL + path, { method: init.method || (init.body ? "POST" : "GET"), headers, body: init.body ? JSON.stringify(init.body) : undefined });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch {}
    throw new Error(`${res.status}: ${msg}`);
  }
  return res.json() as Promise<T>;
}

// The backend has no list endpoints for conversations/videos, so remember ids locally.
export function loadIds(name: string): string[] { try { return JSON.parse(localStorage.getItem("mirage_" + name) || "[]"); } catch { return []; } }
export function saveId(name: string, id: string) { try { localStorage.setItem("mirage_" + name, JSON.stringify([id, ...loadIds(name).filter((x) => x !== id)].slice(0, 50))); } catch {} }

export const fileUrl = (u: string) => (u.startsWith("http") ? u : API_URL + u);
export const fmtDate = (iso?: string | null) => { if (!iso) return "-"; const d = new Date(iso); return isNaN(+d) ? "-" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); };
export const fmtDur = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`);
/** Authenticated download -> object URL (for owner-only files such as consent audio, which need the x-api-key header). */
export async function fetchBlobUrl(path: string): Promise<string> {
  const res = await fetch(API_URL + path, { headers: { "x-api-key": getKey() } });
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
export type Voice = { id: string; language: string; gender: string; accent: string; default_for_language: boolean };
export type Lang = { code: string; name: string; stt: boolean; tts: boolean; default_voice: string };
export type Turn = { seq: number; role: "user" | "assistant"; text: string; t_ms: number; first_audio_ms: number | null; interrupted: boolean; created_at: string };
export type Webhook = { id: string; url: string; events: string[]; active: boolean; description: string; created_at: string; secret?: string };
export type Delivery = { id: string; endpoint_id: string; event: string; status: string; attempts: number; last_status_code: number | null; last_error: string | null; next_attempt_at: string | null; created_at: string; delivered_at: string | null };
export type ApiKey = { id: string; name: string; prefix: string; created_at: string; last_used_at: string | null; revoked_at: string | null; legacy?: boolean };
export type Video = { id: string; replica_id: string; script: string; status: string; output_url: string | null };
