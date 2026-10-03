"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { BrainCircuit, FileText, Link2, Loader2, Plus, Save, Target, Trash2, Wrench, ShieldAlert, Cpu, Send, Brain, ExternalLink, Play } from "lucide-react";
import { api, Persona, Replica, PersonaConfig, Objective, Guardrail, Tool, ShareLink, Voice, Lang, fmtDate, fmtDur } from "@/lib/api";
import { Modal, Tabs, Section, Toggle, Field, Badge, CopyButton, ConfirmDialog, Spinner, toast } from "@/components/ui";

type PForm = { name: string; system_prompt: string; knowledge: string; replica_id: string; tts_voice: string; llm: string };
const EMPTY: PForm = { name: "", system_prompt: "", knowledge: "", replica_id: "", tts_voice: "default", llm: "ollama/llama3.2:1b" };
const EMPTY_CFG: Omit<PersonaConfig, "persona_id"> = { language: "en", greeting: "", objectives: [], guardrails: [], guardrail_fallback: "Sorry, I can't help with that.", memory_enabled: true, custom_llm: { base_url: "", model: "", has_api_key: false }, stt_model: "" };
type Tab = "general" | "voice" | "behavior" | "model" | "tools" | "share" | "memory";
type Doc = { id: string; title: string; source: string; n_chunks: number; created_at: string };
const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

/* ---------------- knowledge (unchanged behaviour) ---------------- */
function Knowledge({ pid }: { pid: string }) {
  const [docs, setDocs] = useState<Doc[] | null>(null); const [title, setTitle] = useState(""); const [text, setText] = useState(""); const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { try { setDocs(await api<Doc[]>(`/v1/personas/${pid}/knowledge`)); } catch (x) { toast.error(x); setDocs([]); } }, [pid]);
  useEffect(() => { setDocs(null); load(); }, [load]);
  async function add(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try { await api(`/v1/personas/${pid}/knowledge/text`, { body: { title, text } }); setTitle(""); setText(""); toast.success("Document added to knowledge."); load(); }
    catch (x) { toast.error(x); } finally { setBusy(false); }
  }
  async function del(id: string) { try { await api(`/v1/personas/${pid}/knowledge/${id}`, { method: "DELETE" }); load(); } catch (x) { toast.error(x); } }
  return (
    <div className="mt-8 border-t border-white/10 pt-6">
      <h3 className="flex items-center gap-2 font-medium"><BrainCircuit size={17} className="text-mirage-violet" />Knowledge</h3>
      <p className="mb-4 mt-1 text-xs text-gray-400">The persona answers from these documents. Paste pricing, FAQs, policies, anything it should know.</p>
      {docs === null ? <div className="h-12 animate-pulse rounded-xl bg-white/5" /> : docs.length === 0 ? (
        <div className="mb-4 flex items-center gap-3 rounded-xl border border-dashed border-white/15 p-4 text-sm text-gray-500"><FileText size={18} />No documents yet.</div>
      ) : (
        <ul className="mb-4 space-y-2">{docs.map((d) => (
          <li key={d.id} className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-3.5 py-2.5">
            <FileText size={16} className="shrink-0 text-mirage-cyan" />
            <div className="min-w-0 flex-1"><p className="truncate text-sm">{d.title}</p><p className="text-xs text-gray-500">{d.source} - {d.n_chunks} chunks</p></div>
            <button aria-label={`Delete ${d.title}`} onClick={() => del(d.id)} className="rounded-lg p-1.5 text-gray-500 hover:bg-mirage-rose/10 hover:text-mirage-rose"><Trash2 size={15} /></button>
          </li>))}</ul>
      )}
      <form onSubmit={add} className="space-y-3 rounded-xl bg-white/[0.03] p-3.5">
        <input className="input" placeholder="Title, e.g. Pricing" required value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea className="input h-24" placeholder="Paste the text..." required value={text} onChange={(e) => setText(e.target.value)} />
        <button className="btn" disabled={busy}>{busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}Add document</button>
      </form>
    </div>
  );
}

/* ---------------- tools ---------------- */
const SCHEMA_TEMPLATE = `{\n  "type": "object",\n  "properties": {\n    "city": { "type": "string", "description": "City name" }\n  },\n  "required": ["city"]\n}`;
export function validateSchema(txt: string): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  let v: unknown;
  try { v = JSON.parse(txt); } catch (e) { return { ok: false, error: "Not valid JSON: " + (e as Error).message }; }
  if (!v || typeof v !== "object" || Array.isArray(v)) return { ok: false, error: "Schema must be a JSON object." };
  const o = v as Record<string, unknown>;
  if (o.type !== "object") return { ok: false, error: 'Top-level "type" must be "object".' };
  if (o.properties !== undefined && (typeof o.properties !== "object" || o.properties === null || Array.isArray(o.properties))) return { ok: false, error: '"properties" must be an object.' };
  if (o.required !== undefined && (!Array.isArray(o.required) || o.required.some((r) => typeof r !== "string"))) return { ok: false, error: '"required" must be a list of strings.' };
  const props = Object.keys((o.properties as object) || {});
  const missing = ((o.required as string[]) || []).filter((r) => !props.includes(r));
  if (missing.length) return { ok: false, error: `"required" names unknown properties: ${missing.join(", ")}.` };
  return { ok: true, value: o };
}
function sampleArgs(schema: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, def] of Object.entries((schema.properties as Record<string, { type?: string; enum?: unknown[] }>) || {})) {
    out[k] = def.enum?.[0] ?? (def.type === "number" || def.type === "integer" ? 1 : def.type === "boolean" ? true : def.type === "array" ? [] : def.type === "object" ? {} : "example");
  }
  return out;
}

function Tools({ pid }: { pid: string }) {
  const [list, setList] = useState<Tool[] | null>(null); const [busy, setBusy] = useState(false);
  const [f, setF] = useState({ name: "", description: "", url: "", secret: "", timeout: "8" }); const [schema, setSchema] = useState(SCHEMA_TEMPLATE);
  const [del, setDel] = useState<Tool | null>(null); const [probe, setProbe] = useState<Record<string, string>>({});
  const v = useMemo(() => validateSchema(schema), [schema]);
  const load = useCallback(() => api<Tool[]>(`/v1/personas/${pid}/tools`).then(setList).catch((x) => { toast.error(x); setList([]); }), [pid]);
  useEffect(() => { setList(null); load(); }, [load]);
  const payload = (name: string, params: Record<string, unknown>) => JSON.stringify({ tool: name, arguments: sampleArgs(params), conversation_id: "c_example", persona_id: pid }, null, 2);
  async function add(e: React.FormEvent) {
    e.preventDefault(); if (!v.ok) return; setBusy(true);
    try {
      await api(`/v1/personas/${pid}/tools`, { body: { name: f.name, description: f.description, parameters: v.value, webhook_url: f.url, secret: f.secret || undefined, timeout_s: Number(f.timeout) || 8 } });
      setF({ name: "", description: "", url: "", secret: "", timeout: "8" }); setSchema(SCHEMA_TEMPLATE); toast.success("Tool added."); load();
    } catch (x) { toast.error(x); } finally { setBusy(false); }
  }
  // Mirage's server calls the webhook when the model asks for the tool. This browser-side probe sends the same body so you can check reachability;
  // browsers enforce CORS, so a "blocked" result is not proof the endpoint is down for the server.
  async function tryCall(t: { id: string; name: string; webhook_url: string; parameters: Record<string, unknown> }) {
    setProbe((p) => ({ ...p, [t.id]: "sending..." }));
    try {
      const r = await fetch(t.webhook_url, { method: "POST", headers: { "content-type": "application/json" }, body: payload(t.name, t.parameters) });
      setProbe((p) => ({ ...p, [t.id]: `HTTP ${r.status}` }));
    } catch { setProbe((p) => ({ ...p, [t.id]: "Blocked by the browser (CORS or unreachable). The Mirage server is not subject to CORS." })); }
  }
  return (
    <div>
      <Section title="Tools" icon={<Wrench size={16} className="text-mirage-amber" />} hint="Function calling: when the model decides to use a tool, Mirage POSTs the arguments to your webhook, then the agent speaks the result.">
        {list === null ? <div className="h-12 animate-pulse rounded-xl bg-white/5" /> : list.length === 0 ? (
          <div className="flex items-center gap-3 rounded-xl border border-dashed border-white/15 p-4 text-sm text-gray-500"><Wrench size={18} />No tools yet.</div>
        ) : (
          <ul className="space-y-2">{list.map((t) => (
            <li key={t.id} className="rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-sm text-white">{t.name}{t.has_secret && <span className="ml-2 rounded bg-white/10 px-1.5 py-0.5 font-sans text-[10px] text-gray-300">signed</span>}</p>
                  <p className="mt-0.5 text-xs text-gray-400">{t.description}</p>
                  <p className="mt-1 truncate font-mono text-[11px] text-gray-500">{t.webhook_url} - {t.timeout_s}s timeout</p>
                </div>
                <button aria-label={`Delete tool ${t.name}`} onClick={() => setDel(t)} className="rounded-lg p-1.5 text-gray-500 hover:bg-mirage-rose/10 hover:text-mirage-rose"><Trash2 size={15} /></button>
              </div>
              <details className="mt-2 text-xs text-gray-400"><summary className="cursor-pointer select-none hover:text-white">Schema and test payload</summary>
                <pre className="mt-2 overflow-x-auto rounded-lg bg-black/40 p-2.5 font-mono text-[11px] text-gray-300">{JSON.stringify(t.parameters, null, 2)}</pre>
                <p className="mb-1 mt-2">Body Mirage will POST:</p>
                <pre className="overflow-x-auto rounded-lg bg-black/40 p-2.5 font-mono text-[11px] text-gray-300">{payload(t.name, t.parameters)}</pre>
                <div className="mt-2 flex flex-wrap items-center gap-2"><button type="button" className="btn-ghost !px-3 !py-1.5 text-xs" onClick={() => tryCall(t)}><Send size={12} />Send sample call</button>{probe[t.id] && <span data-testid="tool-probe" className="text-xs text-gray-400">{probe[t.id]}</span>}</div>
              </details>
            </li>))}</ul>
        )}
      </Section>
      <form onSubmit={add} className="space-y-3 rounded-xl bg-white/[0.03] p-4">
        <p className="text-sm font-medium">Add a tool</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name" hint="letters, digits, underscores"><input className="input font-mono" required pattern="[A-Za-z0-9_\-]+" placeholder="get_weather" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Timeout (s)"><input className="input" type="number" min={1} max={30} value={f.timeout} onChange={(e) => setF({ ...f, timeout: e.target.value })} /></Field>
        </div>
        <Field label="Description (shown to the model)"><input className="input" required placeholder="Look up the current weather for a city" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <Field label="Webhook URL"><input className="input" type="url" required placeholder="https://example.com/tools/weather" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>
        <Field label="Signing secret (optional)" hint="Adds a Mirage-Signature header. Write-only."><input className="input font-mono" autoComplete="off" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} /></Field>
        <div>
          <div className="mb-1.5 flex items-center justify-between"><label className="label !mb-0">Parameters (JSON schema)</label>
            <span className={`text-xs ${v.ok ? "text-mirage-mint" : "text-mirage-rose"}`} data-testid="schema-status">{v.ok ? "Valid schema" : "Invalid"}</span></div>
          <textarea spellCheck={false} aria-label="Parameters JSON schema" className={`input h-40 font-mono text-xs ${v.ok ? "" : "!border-mirage-rose/60"}`} value={schema} onChange={(e) => setSchema(e.target.value)} />
          {!v.ok && <p className="mt-1 text-xs text-mirage-rose">{v.error}</p>}
        </div>
        <button className="btn" disabled={busy || !v.ok}>{busy ? <Spinner size={14} /> : <Plus size={14} />}Add tool</button>
      </form>
      <ConfirmDialog open={!!del} title="Delete tool?" body={<>Remove <b className="font-mono">{del?.name}</b>? The agent will stop calling it immediately.</>} confirmLabel="Delete tool" onClose={() => setDel(null)}
        onConfirm={async () => { try { await api(`/v1/personas/${pid}/tools/${del!.id}`, { method: "DELETE" }); setDel(null); load(); } catch (x) { toast.error(x); } }} />
    </div>
  );
}

/* ---------------- share links ---------------- */
function Share({ pid }: { pid: string }) {
  const [list, setList] = useState<ShareLink[] | null>(null); const [busy, setBusy] = useState(false); const [revoke, setRevoke] = useState<ShareLink | null>(null);
  const [f, setF] = useState({ label: "", max_seconds: "300", max_total_seconds: "3600", expires: "" });
  const load = useCallback(() => api<ShareLink[]>(`/v1/personas/${pid}/share`).then(setList).catch((x) => { toast.error(x); setList([]); }), [pid]);
  useEffect(() => { setList(null); load(); }, [load]);
  async function create(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try {
      await api(`/v1/personas/${pid}/share`, { body: { label: f.label, max_seconds: Number(f.max_seconds), max_total_seconds: Number(f.max_total_seconds), expires_in_hours: f.expires ? Number(f.expires) : undefined } });
      setF({ ...f, label: "" }); toast.success("Share link created."); load();
    } catch (x) { toast.error(x); } finally { setBusy(false); }
  }
  return (
    <div>
      <Section title="Guest links" icon={<Link2 size={16} className="text-mirage-cyan" />} hint="Anyone with the link can talk to this persona without an account. Guest minutes are charged to your credits, capped per session and in total.">
        {list === null ? <div className="h-12 animate-pulse rounded-xl bg-white/5" /> : list.length === 0 ? (
          <div className="flex items-center gap-3 rounded-xl border border-dashed border-white/15 p-4 text-sm text-gray-500"><Link2 size={18} />No guest links yet.</div>
        ) : (
          <ul className="space-y-2">{list.map((l) => {
            const pct = Math.min(100, (l.used_seconds / Math.max(1, l.max_total_seconds)) * 100);
            return (
              <li key={l.token} className={`rounded-xl border border-white/10 bg-white/[0.03] p-3.5 ${l.revoked ? "opacity-60" : ""}`}>
                <div className="flex items-center gap-2"><p className="min-w-0 flex-1 truncate text-sm font-medium">{l.label || "Untitled link"}</p><Badge s={l.revoked ? "revoked" : "active"} /></div>
                <div className="mt-2 flex items-center gap-2 rounded-lg bg-black/40 px-2.5 py-1.5"><code className="min-w-0 flex-1 truncate font-mono text-[11px] text-gray-300" data-testid="guest-url">{l.url}</code><CopyButton text={l.url} label="" /><a aria-label="Open guest page" href={l.url} target="_blank" rel="noreferrer" className="rounded-lg border border-white/10 bg-white/5 p-1.5 text-gray-300 hover:text-white"><ExternalLink size={13} /></a></div>
                <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-white/10"><div className="h-full bg-mirage-gradient" style={{ width: pct + "%" }} /></div>
                <p className="mt-1.5 text-xs text-gray-500">{fmtDur(l.used_seconds)} of {fmtDur(l.max_total_seconds)} used - {l.sessions_started} sessions - {fmtDur(l.max_seconds)} each{l.expires_at ? ` - expires ${fmtDate(l.expires_at)}` : ""}</p>
                {!l.revoked && <button className="mt-2 text-xs text-mirage-rose hover:underline" onClick={() => setRevoke(l)}>Revoke link</button>}
              </li>);
          })}</ul>
        )}
      </Section>
      <form onSubmit={create} className="space-y-3 rounded-xl bg-white/[0.03] p-4">
        <p className="text-sm font-medium">Create a guest link</p>
        <Field label="Label"><input className="input" placeholder="e.g. Website demo" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></Field>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Max per session (s)"><input className="input" type="number" min={10} max={3600} value={f.max_seconds} onChange={(e) => setF({ ...f, max_seconds: e.target.value })} /></Field>
          <Field label="Total cap (s)"><input className="input" type="number" min={10} max={86400} value={f.max_total_seconds} onChange={(e) => setF({ ...f, max_total_seconds: e.target.value })} /></Field>
          <Field label="Expires in (hours)"><input className="input" type="number" min={1} placeholder="never" value={f.expires} onChange={(e) => setF({ ...f, expires: e.target.value })} /></Field>
        </div>
        <button className="btn" disabled={busy}>{busy ? <Spinner size={14} /> : <Plus size={14} />}Create link</button>
      </form>
      <ConfirmDialog open={!!revoke} title="Revoke this link?" body="Guests who open it will see an error and running sessions end at their next check. This cannot be undone." confirmLabel="Revoke" onClose={() => setRevoke(null)}
        onConfirm={async () => { try { await api(`/v1/share/${revoke!.token}`, { method: "DELETE" }); setRevoke(null); toast.success("Link revoked."); load(); } catch (x) { toast.error(x); } }} />
    </div>
  );
}

/* ---------------- memories ---------------- */
type Mem = string | { id?: string; summary?: string; conversation_id?: string | null; created_at?: string };
function Memories({ pid }: { pid: string }) {
  const [list, setList] = useState<Mem[] | null>(null);
  useEffect(() => { setList(null); api<Mem[]>(`/v1/personas/${pid}/memories?limit=30`).then(setList).catch((x) => { toast.error(x); setList([]); }); }, [pid]);
  return (
    <Section title="Memories" icon={<Brain size={16} className="text-mirage-violet" />} hint="After each conversation Mirage writes a short summary. The latest ones are recalled in the next conversation (per participant).">
      {list === null ? <div className="h-12 animate-pulse rounded-xl bg-white/5" /> : list.length === 0 ? (
        <div className="flex items-center gap-3 rounded-xl border border-dashed border-white/15 p-4 text-sm text-gray-500"><Brain size={18} />No memories yet. They appear after a conversation with some turns ends.</div>
      ) : (
        <ul className="space-y-2">{list.map((m, i) => {
          const o = typeof m === "string" ? { summary: m } : m;
          return <li key={o.id ?? i} className="rounded-xl border border-white/10 bg-white/[0.03] p-3.5 text-sm text-gray-200">{o.summary || <span className="text-gray-500">(empty summary)</span>}
            <p className="mt-1.5 font-mono text-[11px] text-gray-500">{o.conversation_id ?? ""} {o.created_at ? "- " + fmtDate(o.created_at) : ""}</p></li>;
        })}</ul>
      )}
    </Section>
  );
}

/* ---------------- drawer ---------------- */
export default function PersonaDrawer({ open, onClose, persona, reps, onSaved }: { open: boolean; onClose: () => void; persona: Persona | null; reps: Replica[]; onSaved: () => void }) {
  const [pid, setPid] = useState<string | null>(null); const [f, setF] = useState<PForm>(EMPTY); const [tab, setTab] = useState<Tab>("general");
  const [cfg, setCfg] = useState(EMPTY_CFG); const [apiKey, setApiKey] = useState(""); const [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState(false); const [langs, setLangs] = useState<Lang[]>([]); const [voices, setVoices] = useState<Voice[]>([]);
  const [voicesErr, setVoicesErr] = useState(false); const [cfgLoaded, setCfgLoaded] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTab("general"); setApiKey(""); setClearKey(false); setCfgLoaded(false);
    if (persona) { setPid(persona.id); setF({ name: persona.name, system_prompt: persona.system_prompt, knowledge: persona.knowledge || "", replica_id: persona.replica_id || "", tts_voice: persona.tts_voice, llm: persona.llm }); }
    else { setPid(null); setF(EMPTY); setCfg(EMPTY_CFG); }
  }, [open, persona]);
  useEffect(() => {
    if (!open || !pid) return;
    api<PersonaConfig>(`/v1/personas/${pid}/config`).then((c) => { const { persona_id: _p, ...rest } = c; void _p; setCfg(rest); setCfgLoaded(true); }).catch((x) => { toast.error(x); setCfgLoaded(true); });
  }, [open, pid]);
  useEffect(() => {
    if (!open || voices.length) return;
    api<{ languages: Lang[]; voices: Voice[] }>("/v1/voices").then((r) => { setLangs(r.languages); setVoices(r.voices); }).catch(() => setVoicesErr(true));
  }, [open, voices.length]);

  const set = (k: keyof PForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const lang = langs.find((l) => l.code === cfg.language);
  const shownVoices = voices.filter((v) => cfg.language === "auto" || v.language === cfg.language);
  const ttsOk = cfg.language === "auto" || lang?.tts !== false;

  async function savePersona() {
    const body = { ...f, replica_id: f.replica_id || null };
    if (pid) { await api(`/v1/personas/${pid}`, { method: "PUT", body }); return pid; }
    const p = await api<Persona>("/v1/personas", { body }); setPid(p.id); return p.id;
  }
  async function saveConfig(id: string) {
    const custom: Record<string, unknown> = { base_url: cfg.custom_llm.base_url, model: cfg.custom_llm.model };
    if (clearKey) custom.api_key = ""; else if (apiKey) custom.api_key = apiKey;
    const c = await api<PersonaConfig>(`/v1/personas/${id}/config`, { method: "PUT", body: {
      language: cfg.language, greeting: cfg.greeting, guardrail_fallback: cfg.guardrail_fallback, memory_enabled: cfg.memory_enabled, stt_model: cfg.stt_model,
      objectives: cfg.objectives.filter((o) => o.name.trim()), guardrails: cfg.guardrails.filter((g) => g.name.trim()), custom_llm: custom } });
    const { persona_id: _p, ...rest } = c; void _p; setCfg(rest); setApiKey(""); setClearKey(false);
  }
  async function submitGeneral(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try { const had = !!pid; await savePersona(); toast.success(had ? "Persona saved." : "Persona created. Now add some knowledge below."); onSaved(); }
    catch (x) { toast.error(x); } finally { setBusy(false); }
  }
  async function submitConfig(label: string, alsoPersona = false) {
    if (!pid) return; setBusy(true);
    try { if (alsoPersona) await savePersona(); await saveConfig(pid); toast.success(label + " saved."); onSaved(); }
    catch (x) { toast.error(x); } finally { setBusy(false); }
  }
  const SaveBtn = ({ label, persona: p = false }: { label: string; persona?: boolean }) => (
    <div className="sticky bottom-0 -mx-5 -mb-5 mt-6 border-t border-white/10 bg-ink-2/95 px-5 py-3 backdrop-blur">
      <button type="button" className="btn-grad w-full" disabled={busy} onClick={() => submitConfig(label, p)}>{busy ? <Spinner /> : <Save size={15} />}Save {label.toLowerCase()}</button>
    </div>
  );

  const tabs: { id: Tab; label: string; badge?: number }[] = [
    { id: "general", label: "General" }, { id: "voice", label: "Voice & language" }, { id: "behavior", label: "Behavior", badge: cfg.objectives.length + cfg.guardrails.length },
    { id: "model", label: "Model" }, { id: "tools", label: "Tools" }, { id: "share", label: "Share" }, { id: "memory", label: "Memories" },
  ];
  const locked = !pid;
  const updObj = (i: number, patch: Partial<Objective>) => setCfg({ ...cfg, objectives: cfg.objectives.map((o, j) => (j === i ? { ...o, ...patch } : o)) });
  const updGr = (i: number, patch: Partial<Guardrail>) => setCfg({ ...cfg, guardrails: cfg.guardrails.map((g, j) => (j === i ? { ...g, ...patch } : g)) });

  return (
    <Modal side wide open={open} onClose={onClose} title={persona || pid ? "Edit persona" : "New persona"}>
      <Tabs tabs={locked ? tabs.slice(0, 1) : tabs} value={tab} onChange={setTab} />
      {tab === "general" && (<>
        <form onSubmit={submitGeneral} className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div><label className="label">Name</label><input className="input" required value={f.name} onChange={set("name")} /></div>
            <div><label className="label">Replica</label><select className="input" value={f.replica_id} onChange={set("replica_id")}><option value="">None</option>{reps.map((r) => <option key={r.id} value={r.id}>{r.name} ({r.status})</option>)}</select></div>
            <div className="sm:col-span-2"><label className="label">LLM</label><input className="input" value={f.llm} onChange={set("llm")} /></div>
          </div>
          <div><label className="label">System prompt</label><textarea className="input h-28" required placeholder="You are a friendly sales rep who..." value={f.system_prompt} onChange={set("system_prompt")} /></div>
          <div><label className="label">Quick notes (always in context)</label><textarea className="input h-20" value={f.knowledge} onChange={set("knowledge")} /></div>
          <button className="btn-grad w-full" disabled={busy}>{busy && <Loader2 size={15} className="animate-spin" />}{pid ? "Save changes" : "Create persona"}</button>
        </form>
        {pid ? <Knowledge pid={pid} /> : <p className="mt-6 rounded-xl border border-dashed border-white/15 p-4 text-xs text-gray-500">Create the persona first, then you can upload knowledge documents and configure voice, objectives, tools and sharing here.</p>}
      </>)}

      {tab === "voice" && !locked && (<>
        {!cfgLoaded ? <div className="h-24 animate-pulse rounded-xl bg-white/5" /> : (<div className="space-y-4">
          <Field label="Language" hint={lang && !lang.tts && cfg.language !== "auto" ? "Speech recognition only: the agent cannot speak this language yet (no voice available)." : "Used for speech recognition and the agent's voice. 'Auto' detects each utterance."}>
            <select className="input" aria-label="Language" value={cfg.language} onChange={(e) => { setCfg({ ...cfg, language: e.target.value }); setF({ ...f, tts_voice: "default" }); }}>
              <option value="auto">Auto-detect</option>{langs.map((l) => <option key={l.code} value={l.code}>{l.name}{l.tts ? "" : " (listen only)"}</option>)}
              {voicesErr && <option value={cfg.language}>{cfg.language}</option>}
            </select>
          </Field>
          <Field label="Voice" hint={voicesErr ? "Could not load voices from this server; you can still type a voice id." : undefined}>
            {voicesErr ? <input className="input" value={f.tts_voice} onChange={set("tts_voice")} /> : (
              <select className="input" aria-label="Voice" disabled={!ttsOk} value={f.tts_voice} onChange={set("tts_voice")}>
                <option value="default">Language default{lang?.default_voice ? ` (${lang.default_voice})` : ""}</option>
                {shownVoices.map((v) => <option key={v.id} value={v.id}>{v.id} - {v.gender}, {v.accent}{v.default_for_language ? " (default)" : ""}</option>)}
                {f.tts_voice !== "default" && !shownVoices.some((v) => v.id === f.tts_voice) && <option value={f.tts_voice}>{f.tts_voice} (current)</option>}
              </select>)}
          </Field>
          <Field label="Speech-recognition model (optional)" hint="Leave empty for the automatic choice (larger model for Hindi, CJK, Arabic, etc.)."><input className="input font-mono" placeholder="e.g. small" value={cfg.stt_model} onChange={(e) => setCfg({ ...cfg, stt_model: e.target.value })} /></Field>
          <Toggle checked={cfg.memory_enabled} onChange={(v) => setCfg({ ...cfg, memory_enabled: v })} label="Remember past conversations" hint="Recall summaries from earlier conversations with the same participant." />
        </div>)}
        <SaveBtn label="Voice and language" persona />
      </>)}

      {tab === "behavior" && !locked && (<>
        {!cfgLoaded ? <div className="h-24 animate-pulse rounded-xl bg-white/5" /> : (<>
          <Section title="Greeting" hint="If set, the agent speaks first. Use {{first_name}}-style variables supplied when you start a conversation.">
            <input className="input" aria-label="Greeting" placeholder="Hi {{first_name}}, thanks for joining. How can I help?" value={cfg.greeting} onChange={(e) => setCfg({ ...cfg, greeting: e.target.value })} />
          </Section>
          <Section title="Objectives" icon={<Target size={16} className="text-mirage-mint" />} hint="Goals the agent works towards. A judge model marks each complete and extracts the variables you name. Results show in the conversation detail."
            action={<button type="button" className="btn-ghost !px-3 !py-1.5 text-xs" onClick={() => setCfg({ ...cfg, objectives: [...cfg.objectives, { name: "", description: "", success_criteria: "", output_variables: [] }] })}><Plus size={13} />Add</button>}>
            {cfg.objectives.length === 0 && <p className="rounded-xl border border-dashed border-white/15 p-3 text-xs text-gray-500">No objectives. Example: collect the visitor&apos;s email and budget.</p>}
            <div className="space-y-3">{cfg.objectives.map((o, i) => (
              <div key={i} className="space-y-2.5 rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
                <div className="flex items-end gap-2"><div className="flex-1"><label className="label">Name</label><input className="input" aria-label={`Objective ${i + 1} name`} placeholder="capture_email" value={o.name} onChange={(e) => updObj(i, { name: e.target.value })} /></div>
                  <button type="button" aria-label={`Remove objective ${i + 1}`} className="mb-0.5 rounded-lg p-2 text-gray-500 hover:bg-mirage-rose/10 hover:text-mirage-rose" onClick={() => setCfg({ ...cfg, objectives: cfg.objectives.filter((_, j) => j !== i) })}><Trash2 size={15} /></button></div>
                <div><label className="label">Description</label><input className="input" aria-label={`Objective ${i + 1} description`} placeholder="What the agent should achieve" value={o.description} onChange={(e) => updObj(i, { description: e.target.value })} /></div>
                <div><label className="label">Success criteria</label><input className="input" aria-label={`Objective ${i + 1} success criteria`} placeholder="The user has said an email address" value={o.success_criteria} onChange={(e) => updObj(i, { success_criteria: e.target.value })} /></div>
                <div><label className="label">Output variables</label><input className="input font-mono text-xs" aria-label={`Objective ${i + 1} output variables`} placeholder="email, budget (comma separated)" value={o.output_variables.join(", ")} onChange={(e) => updObj(i, { output_variables: csv(e.target.value) })} /></div>
              </div>))}</div>
          </Section>
          <Section title="Guardrails" icon={<ShieldAlert size={16} className="text-mirage-rose" />} hint="Hard rules in the prompt, plus forbidden phrases blocked from the spoken output. On a hit the agent says the fallback line instead."
            action={<button type="button" className="btn-ghost !px-3 !py-1.5 text-xs" onClick={() => setCfg({ ...cfg, guardrails: [...cfg.guardrails, { name: "", rule: "", forbidden_phrases: [] }] })}><Plus size={13} />Add</button>}>
            {cfg.guardrails.length === 0 && <p className="rounded-xl border border-dashed border-white/15 p-3 text-xs text-gray-500">No guardrails. Example: never quote prices that are not in the knowledge base.</p>}
            <div className="space-y-3">{cfg.guardrails.map((g, i) => (
              <div key={i} className="space-y-2.5 rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
                <div className="flex items-end gap-2"><div className="flex-1"><label className="label">Name</label><input className="input" aria-label={`Guardrail ${i + 1} name`} placeholder="no_legal_advice" value={g.name} onChange={(e) => updGr(i, { name: e.target.value })} /></div>
                  <button type="button" aria-label={`Remove guardrail ${i + 1}`} className="mb-0.5 rounded-lg p-2 text-gray-500 hover:bg-mirage-rose/10 hover:text-mirage-rose" onClick={() => setCfg({ ...cfg, guardrails: cfg.guardrails.filter((_, j) => j !== i) })}><Trash2 size={15} /></button></div>
                <div><label className="label">Rule</label><input className="input" aria-label={`Guardrail ${i + 1} rule`} placeholder="Never give legal advice" value={g.rule} onChange={(e) => updGr(i, { rule: e.target.value })} /></div>
                <div><label className="label">Forbidden phrases</label><input className="input font-mono text-xs" aria-label={`Guardrail ${i + 1} forbidden phrases`} placeholder="comma separated" value={g.forbidden_phrases.join(", ")} onChange={(e) => updGr(i, { forbidden_phrases: csv(e.target.value) })} /></div>
              </div>))}</div>
            <div className="mt-3"><Field label="Fallback line"><input className="input" aria-label="Guardrail fallback" value={cfg.guardrail_fallback} onChange={(e) => setCfg({ ...cfg, guardrail_fallback: e.target.value })} /></Field></div>
          </Section>
        </>)}
        <SaveBtn label="Behavior" />
      </>)}

      {tab === "model" && !locked && (<>
        {!cfgLoaded ? <div className="h-24 animate-pulse rounded-xl bg-white/5" /> : (
          <Section title="Custom LLM endpoint" icon={<Cpu size={16} className="text-mirage-cyan" />} hint="Point the persona at any OpenAI-compatible /chat/completions endpoint (vLLM, LM Studio, OpenAI, Groq, Together). Leave empty to use the LLM from the General tab.">
            <div className="space-y-3">
              <Field label="Base URL"><input className="input" type="url" placeholder="https://api.openai.com/v1" value={cfg.custom_llm.base_url} onChange={(e) => setCfg({ ...cfg, custom_llm: { ...cfg.custom_llm, base_url: e.target.value } })} /></Field>
              <Field label="Model"><input className="input font-mono" placeholder="gpt-4o-mini" value={cfg.custom_llm.model} onChange={(e) => setCfg({ ...cfg, custom_llm: { ...cfg.custom_llm, model: e.target.value } })} /></Field>
              <Field label="API key" hint="Write-only. It is stored encrypted and never shown again.">
                <input className="input font-mono" type="password" autoComplete="new-password" aria-label="Custom LLM API key" disabled={clearKey}
                  placeholder={cfg.custom_llm.has_api_key ? "A key is saved. Type to replace it." : "sk-..."} value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
              </Field>
              <div className="flex items-center gap-3 text-xs">
                {cfg.custom_llm.has_api_key ? <span className="inline-flex items-center gap-1.5 rounded-full bg-mirage-mint/10 px-2.5 py-1 text-mirage-mint" data-testid="key-saved">Key saved</span> : <span className="rounded-full bg-white/5 px-2.5 py-1 text-gray-400">No key saved</span>}
                {cfg.custom_llm.has_api_key && <label className="flex cursor-pointer items-center gap-1.5 text-gray-400"><input type="checkbox" checked={clearKey} onChange={(e) => { setClearKey(e.target.checked); if (e.target.checked) setApiKey(""); }} />Remove the saved key on save</label>}
              </div>
            </div>
          </Section>
        )}
        <SaveBtn label="Model" />
      </>)}

      {tab === "tools" && pid && <Tools pid={pid} />}
      {tab === "share" && pid && <Share pid={pid} />}
      {tab === "memory" && pid && <Memories pid={pid} />}
      {pid && tab === "general" && <p className="mt-6 flex items-center gap-1.5 font-mono text-[11px] text-gray-600"><Play size={10} />{pid}</p>}
    </Modal>
  );
}
