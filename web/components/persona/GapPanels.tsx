"use client";
import { useCallback, useEffect, useState } from "react";
import { CalendarClock, Globe, Mic2, RefreshCw, Trash2, Plus, SlidersHorizontal, Download } from "lucide-react";
import { api, API_URL, errText, ShareLink, fmtDate } from "@/lib/api";
import { Section, Field, Toggle, toast, Spinner } from "@/components/ui";

/* ---- knowledge from a web page (added under the Knowledge list) ---- */
type Source = { doc_id: string; url: string; fetched_at: string };
export function KnowledgeUrl({ pid, onChanged }: { pid: string; onChanged: () => void }) {
  const [url, setUrl] = useState(""); const [busy, setBusy] = useState(false); const [src, setSrc] = useState<Source[]>([]); const [spin, setSpin] = useState("");
  const load = useCallback(() => api<Source[]>(`/v1/personas/${pid}/knowledge-sources`).then(setSrc).catch(() => setSrc([])), [pid]);
  useEffect(() => { load(); }, [load]);
  async function add(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try { await api(`/v1/personas/${pid}/knowledge/url`, { body: { url } }); setUrl(""); toast.success("Page added to knowledge."); load(); onChanged(); }
    catch (x) { toast.error(errText(x)); } finally { setBusy(false); }
  }
  async function refresh(id: string) {
    setSpin(id);
    try { const r = await api<{ changed: boolean }>(`/v1/personas/${pid}/knowledge/${id}/refresh`, { method: "POST", body: {} }); toast.success(r.changed ? "Page changed, knowledge updated." : "Page unchanged."); load(); onChanged(); }
    catch (x) { toast.error(errText(x)); } finally { setSpin(""); }
  }
  return (
    <div className="mt-4" data-testid="knowledge-url">
      <form onSubmit={add} className="space-y-2 rounded-xl bg-white/[0.03] p-3.5">
        <p className="flex items-center gap-2 text-sm font-medium"><Globe size={15} className="text-mirage-cyan" />Add a web page</p>
        <input className="input" type="url" required placeholder="https://example.com/pricing" aria-label="Page URL" value={url} onChange={(e) => setUrl(e.target.value)} />
        <p className="text-xs text-gray-500">Public pages and PDFs only. Text is extracted (scripts and navigation dropped) and the agent can cite it.</p>
        <button className="btn" disabled={busy}>{busy ? <Spinner size={14} /> : <Plus size={14} />}Fetch and add</button>
      </form>
      {src.length > 0 && <ul className="mt-3 space-y-1.5">{src.map((s) => (
        <li key={s.doc_id} className="flex items-center gap-2 text-xs text-gray-400"><Globe size={12} /><span className="min-w-0 flex-1 truncate">{s.url}</span><span className="text-gray-600">{fmtDate(s.fetched_at)}</span>
          <button aria-label={`Refresh ${s.url}`} disabled={spin === s.doc_id} onClick={() => refresh(s.doc_id)} className="rounded-lg p-1.5 hover:bg-white/10 hover:text-white">{spin === s.doc_id ? <Spinner size={13} /> : <RefreshCw size={13} />}</button></li>))}</ul>}
    </div>
  );
}

/* ---- pronunciation glossary + interruption tuning (Voice tab) ---- */
type Pron = { id: string; term: string; replacement: string; case_sensitive: boolean };
type Tuning = { interruption_sensitivity: number; allow_interruptions: boolean; turn_patience_ms: number };
export function VoiceExtras({ pid }: { pid: string }) {
  const [rows, setRows] = useState<Pron[] | null>(null); const [term, setTerm] = useState(""); const [rep, setRep] = useState(""); const [test, setTest] = useState(""); const [heard, setHeard] = useState("");
  const [t, setT] = useState<Tuning | null>(null); const [saving, setSaving] = useState(false);
  const load = useCallback(() => api<Pron[]>(`/v1/personas/${pid}/pronunciations`).then(setRows).catch((x) => { toast.error(errText(x)); setRows([]); }), [pid]);
  useEffect(() => { load(); api<Tuning>(`/v1/personas/${pid}/voice-tuning`).then(setT).catch(() => {}); }, [pid, load]);
  async function add(e: React.FormEvent) { e.preventDefault(); try { await api(`/v1/personas/${pid}/pronunciations`, { body: { term, replacement: rep } }); setTerm(""); setRep(""); load(); } catch (x) { toast.error(errText(x)); } }
  async function preview() { try { const r = await api<{ spoken_as: string }>(`/v1/personas/${pid}/pronunciations/preview`, { body: { text: test } }); setHeard(r.spoken_as); } catch (x) { toast.error(errText(x)); } }
  async function saveT() { if (!t) return; setSaving(true); try { setT(await api<Tuning>(`/v1/personas/${pid}/voice-tuning`, { method: "PUT", body: t })); toast.success("Saved. Applies to the next conversation."); } catch (x) { toast.error(errText(x)); } finally { setSaving(false); } }
  return (
    <div className="mt-8 space-y-8 border-t border-white/10 pt-6">
      <Section title="Interruptions and turn-taking" icon={<SlidersHorizontal size={16} className="text-mirage-violet" />} hint="How easily the user can cut the agent off, and how long the agent waits after the user stops talking.">
        {!t ? <div className="h-16 animate-pulse rounded-xl bg-white/5" /> : (<div className="space-y-4" data-testid="tuning">
          <Toggle checked={t.allow_interruptions} onChange={(v) => setT({ ...t, allow_interruptions: v })} label="Allow interruptions" hint="Off: the agent always finishes its sentence (compliance scripts, announcements)." />
          <Field label={`Interruption sensitivity: ${Math.round(t.interruption_sensitivity * 100)}%`} hint="Low needs longer, clearer speech to interrupt; high reacts to a brief word.">
            <input type="range" min={0} max={1} step={0.05} disabled={!t.allow_interruptions} aria-label="Interruption sensitivity" className="w-full accent-mirage-violet" value={t.interruption_sensitivity} onChange={(e) => setT({ ...t, interruption_sensitivity: Number(e.target.value) })} />
          </Field>
          <Field label={`Turn patience: ${t.turn_patience_ms} ms`} hint="Silence before the agent decides the user has finished. Raise for slow or thoughtful speakers.">
            <input type="range" min={300} max={3000} step={100} aria-label="Turn patience" className="w-full accent-mirage-violet" value={t.turn_patience_ms} onChange={(e) => setT({ ...t, turn_patience_ms: Number(e.target.value) })} />
          </Field>
          <button className="btn" onClick={saveT} disabled={saving}>{saving ? <Spinner size={14} /> : null}Save tuning</button>
        </div>)}
      </Section>
      <Section title="Pronunciation" icon={<Mic2 size={16} className="text-mirage-cyan" />} hint="Teach the voice how to say names and acronyms. Write a respelling; the transcript still shows the original word.">
        {rows === null ? <div className="h-12 animate-pulse rounded-xl bg-white/5" /> : rows.length === 0 ? <p className="mb-3 rounded-xl border border-dashed border-white/15 p-3 text-sm text-gray-500">No entries yet.</p> : (
          <ul className="mb-3 space-y-1.5" data-testid="pron-list">{rows.map((r) => (
            <li key={r.id} className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm"><span className="font-mono">{r.term}</span><span className="text-gray-500">says</span><span className="min-w-0 flex-1 truncate font-mono text-mirage-cyan">{r.replacement}</span>
              <button aria-label={`Delete ${r.term}`} onClick={async () => { await api(`/v1/personas/${pid}/pronunciations/${r.id}`, { method: "DELETE" }).catch((x) => toast.error(errText(x))); load(); }} className="rounded-lg p-1.5 text-gray-500 hover:bg-mirage-rose/10 hover:text-mirage-rose"><Trash2 size={14} /></button></li>))}</ul>)}
        <form onSubmit={add} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
          <input className="input" required placeholder="Word, e.g. Nuvee" aria-label="Word" value={term} onChange={(e) => setTerm(e.target.value)} />
          <input className="input" required placeholder="Say it as, e.g. NOO-vee" aria-label="Say it as" value={rep} onChange={(e) => setRep(e.target.value)} />
          <button className="btn"><Plus size={14} />Add</button>
        </form>
        <div className="mt-3 flex gap-2"><input className="input" placeholder="Try a sentence..." aria-label="Test sentence" value={test} onChange={(e) => setTest(e.target.value)} /><button type="button" className="btn" onClick={preview} disabled={!test}>Preview</button></div>
        {heard && <p className="mt-2 rounded-lg bg-black/40 p-2.5 font-mono text-xs text-gray-300" data-testid="pron-heard">{heard}</p>}
      </Section>
    </div>
  );
}

/* ---- scheduled calls (Share tab) ---- */
type Sched = { starts_at: string; ends_at: string; invitee_name: string; invitee_email: string; note: string };
export function ScheduleCall({ pid }: { pid: string }) {
  const [links, setLinks] = useState<ShareLink[]>([]); const [tok, setTok] = useState(""); const [when, setWhen] = useState(""); const [mins, setMins] = useState("30");
  const [name, setName] = useState(""); const [email, setEmail] = useState(""); const [note, setNote] = useState(""); const [cur, setCur] = useState<Sched | null>(null); const [busy, setBusy] = useState(false);
  useEffect(() => { api<ShareLink[]>(`/v1/personas/${pid}/share`).then((l) => { const a = l.filter((x) => !x.revoked); setLinks(a); setTok((t) => t || a[0]?.token || ""); }).catch(() => {}); }, [pid]);
  useEffect(() => { setCur(null); if (tok) api<Sched>(`/v1/share/${tok}/schedule`).then(setCur).catch(() => {}); }, [tok]);
  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try { setCur(await api<Sched>(`/v1/share/${tok}/schedule`, { method: "PUT", body: { starts_at: new Date(when).toISOString(), duration_minutes: Number(mins), invitee_name: name, invitee_email: email, note } })); toast.success("Call scheduled. The link opens at that time."); }
    catch (x) { toast.error(errText(x)); } finally { setBusy(false); }
  }
  async function clear() { try { await api(`/v1/share/${tok}/schedule`, { method: "DELETE" }); setCur(null); toast.success("Schedule removed; the link is open again."); } catch (x) { toast.error(errText(x)); } }
  if (links.length === 0) return null;
  return (
    <div className="mt-8 border-t border-white/10 pt-6" data-testid="schedule">
      <Section title="Schedule a call" icon={<CalendarClock size={16} className="text-mirage-amber" />} hint="Turn a guest link into an appointment: it only opens in the window you pick. Send the invitee the link and a calendar file.">
        <form onSubmit={save} className="space-y-3 rounded-xl bg-white/[0.03] p-4">
          <Field label="Guest link"><select className="input" aria-label="Guest link" value={tok} onChange={(e) => setTok(e.target.value)}>{links.map((l) => <option key={l.token} value={l.token}>{l.label || l.token}</option>)}</select></Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Starts"><input className="input" type="datetime-local" required aria-label="Starts" value={when} onChange={(e) => setWhen(e.target.value)} /></Field>
            <Field label="Duration (minutes)"><input className="input" type="number" min={5} max={480} value={mins} onChange={(e) => setMins(e.target.value)} /></Field>
            <Field label="Invitee name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Invitee email"><input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          </div>
          <Field label="Note"><input className="input" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          <button className="btn" disabled={busy || !tok}>{busy ? <Spinner size={14} /> : <CalendarClock size={14} />}Schedule</button>
        </form>
        {cur && <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-white/10 p-3 text-xs text-gray-300" data-testid="schedule-current">
          <span>Opens {fmtDate(cur.starts_at)}, closes {fmtDate(cur.ends_at)}{cur.invitee_name ? ` - for ${cur.invitee_name}` : ""}</span>
          <a className="inline-flex items-center gap-1 text-mirage-cyan hover:underline" href={`${API_URL}/v1/guest/${tok}/schedule.ics`}><Download size={12} />Calendar (.ics)</a>
          <button className="text-mirage-rose hover:underline" onClick={clear}>Remove schedule</button></div>}
      </Section>
    </div>
  );
}
