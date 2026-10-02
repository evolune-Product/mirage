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

export type Replica = { id: string; name: string; train_video_url: string; status: string; created_at: string };
export type Persona = { id: string; name: string; system_prompt: string; replica_id: string | null; llm: string; tts_voice: string; knowledge: string };
export type Conversation = { id: string; persona_id: string; status: string; room_url: string; seconds_used: number };
export type Video = { id: string; replica_id: string; script: string; status: string; output_url: string | null };
