"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { Eye, Headphones, Loader2, MessagesSquare, Play, Square, Clock, ChevronDown, Link2, ExternalLink } from "lucide-react";
import { API_URL, api, Perception, Conversation, Persona, ShareLink, getKey, saveId, fmtDate, fmtDur } from "@/lib/api";
import ConversationDetail from "@/components/conversation/ConversationDetail";
import { Shell, Badge, Empty, Skeleton, CopyButton, refreshCredits, toast } from "@/components/ui";

export default function Conversations() {
  const [ps, setPs] = useState<Persona[] | null>(null); const [pid, setPid] = useState(""); const [cur, setCur] = useState<Conversation | null>(null);
  const [past, setPast] = useState<Conversation[] | null>(null); const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<Conversation | null>(null); const [opts, setOpts] = useState(false);
  const [o, setO] = useState({ participant_id: "", max_seconds: "", context: "", variables: "" }); const [shares, setShares] = useState<ShareLink[] | null>(null);
  const [perc, setPerc] = useState<Perception | null>(null);
  useEffect(() => { setPerc(null); if (pid) api<Perception>(`/v1/personas/${pid}/perception`).then(setPerc).catch(() => {}); }, [pid]);
  const percOn = !!perc && perc.enabled && perc.consent_acknowledged;
  const loadPast = () => api<Conversation[]>("/v1/conversations").then((l) => setPast([...l].reverse())).catch((x) => { toast.error(x); setPast([]); });
  useEffect(() => {
    api<Persona[]>("/v1/personas").then(async (l) => {
      setPs(l); if (l[0]) setPid(l[0].id);
      const all = await Promise.all(l.map((p) => api<ShareLink[]>(`/v1/personas/${p.id}/share`).catch(() => [] as ShareLink[])));
      setShares(all.flat().filter((x) => !x.revoked));
    }).catch((x) => { toast.error(x); setPs([]); setShares([]); });
    loadPast();
  }, []);
  async function start() {
    setBusy(true);
    try {
      const body: Record<string, unknown> = { persona_id: pid };
      if (o.participant_id) body.participant_id = o.participant_id; if (o.context) body.context = o.context; if (o.max_seconds) body.max_seconds = Number(o.max_seconds);
      if (o.variables.trim()) body.variables = Object.fromEntries(o.variables.split(",").map((kv) => kv.split("=").map((x) => x.trim())).filter((kv) => kv[0] && kv[1] !== undefined));
      const c = await api<Conversation>("/v1/conversations", { body }); saveId("conversations", c.id); setCur(c); loadPast(); }
    catch (x) { toast.error(x); } finally { setBusy(false); }
  }
  async function end() {
    if (!cur) return;
    try { setCur(await api<Conversation>(`/v1/conversations/${cur.id}/end`, { method: "POST", body: {} })); loadPast(); refreshCredits(); toast.success("Conversation ended."); }
    catch (x) { toast.error(x); }
  }
  const src = cur ? `${API_URL}/static/playground.html?cid=${cur.id}&api_key=${encodeURIComponent(getKey())}&api=${encodeURIComponent(API_URL)}` : "";
  const pname = (id: string) => ps?.find((p) => p.id === id)?.name ?? id;

  return (
    <Shell title="Conversations" subtitle="Talk to a persona live. Press Start inside the frame and allow your microphone.">
      {ps === null ? <Skeleton className="h-28" /> : ps.length === 0 ? (
        <Empty kind="conversation" title="You need a persona first" hint="Conversations are held with a persona. Create one, add some knowledge, then come back to talk to it." action={<Link href="/dashboard/personas" className="btn-grad">Create a persona</Link>} />
      ) : (
        <div className="card flex flex-wrap items-end gap-3" data-testid="start-panel">
          <div className="min-w-60 flex-1"><label className="label">Persona</label>
            <select className="input" value={pid} onChange={(e) => setPid(e.target.value)}>{ps.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
          <button type="button" className="btn-ghost" aria-expanded={opts} onClick={() => setOpts(!opts)}>Options<ChevronDown size={14} className={`transition ${opts ? "rotate-180" : ""}`} /></button>
          <button className="btn-grad" disabled={!pid || busy} onClick={start}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}Start conversation</button>
          {opts && (
            <div className="grid w-full gap-3 border-t border-white/10 pt-4 sm:grid-cols-2">
              <div><label className="label">Participant id</label><input className="input" placeholder="memory scope, e.g. user_42" value={o.participant_id} onChange={(e) => setO({ ...o, participant_id: e.target.value })} /></div>
              <div><label className="label">Max seconds</label><input className="input" type="number" min={10} placeholder="no cap" value={o.max_seconds} onChange={(e) => setO({ ...o, max_seconds: e.target.value })} /></div>
              <div className="sm:col-span-2"><label className="label">Variables</label><input className="input font-mono" placeholder="first_name=Ravi, company=Acme" value={o.variables} onChange={(e) => setO({ ...o, variables: e.target.value })} /></div>
              <div className="sm:col-span-2"><label className="label">Extra context</label><textarea className="input h-20" placeholder="Anything special about this call" value={o.context} onChange={(e) => setO({ ...o, context: e.target.value })} /></div>
            </div>)}
        </div>
      )}
      {ps && ps.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-gray-400" data-testid="conv-hints">
          <span className="inline-flex items-center gap-1.5"><Headphones size={13} className="text-mirage-cyan" />Wear headphones: the agent&apos;s voice from speakers can be picked up by your mic and make it interrupt itself.</span>
          {perc && (percOn
            ? <span className="inline-flex items-center gap-1.5 rounded-full bg-mirage-cyan/10 px-2.5 py-1 text-mirage-cyan" data-testid="perception-on"><Eye size={12} />Perception on: {[perc.camera && "camera", perc.screen && "screen share"].filter(Boolean).join(" and ") || "off"}. Press the camera or screen button in the window to opt in; frames are analysed live and not kept{perc.store_frames ? " (storing is enabled for this persona)" : ""}.</span>
            : <span className="inline-flex items-center gap-1.5 text-gray-500"><Eye size={12} />Perception off for this persona (enable it under Personas, Perception).</span>)}
        </div>)}
      <AnimatePresence>
        {cur && (
          <motion.div key={cur.id} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="mt-6 overflow-hidden rounded-2xl border border-white/10 bg-ink-2 shadow-[0_30px_80px_-30px_rgba(124,92,255,.35)]">
            <div className="flex items-center justify-between gap-3 border-b border-white/10 bg-ink-3/60 px-4 py-2.5">
              <div className="flex min-w-0 items-center gap-3"><span className="hidden gap-1.5 sm:flex"><i className="h-2.5 w-2.5 rounded-full bg-mirage-rose/70" /><i className="h-2.5 w-2.5 rounded-full bg-mirage-amber/70" /><i className="h-2.5 w-2.5 rounded-full bg-mirage-mint/70" /></span>
                <span className="truncate font-mono text-xs text-gray-400">{cur.id}</span><Badge s={cur.status} />{cur.status === "ended" && <span className="text-xs text-gray-500">{cur.seconds_used}s used</span>}</div>
              {cur.status !== "ended" && <button className="btn-ghost !px-3 !py-1.5 text-xs" onClick={end}><Square size={12} />End</button>}
            </div>
            <iframe src={src} className="h-[620px] w-full bg-ink" allow="camera; microphone; autoplay; display-capture; clipboard-write; fullscreen" title="Playground" />
          </motion.div>
        )}
      </AnimatePresence>
      {shares && shares.length > 0 && (<>
        <h2 className="mb-3 mt-10 flex items-center gap-2 text-sm font-medium text-gray-300"><Link2 size={14} />Guest links</h2>
        <div className="overflow-hidden rounded-2xl border border-white/10">
          {shares.map((l) => (
            <div key={l.token} className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-white/5 bg-ink-2/60 px-4 py-3 last:border-0">
              <div className="min-w-0 flex-1 basis-48"><p className="truncate text-sm">{l.label || "Untitled link"} <span className="text-gray-500">- {pname(l.persona_id)}</span></p><p className="truncate font-mono text-[11px] text-gray-500">{l.url}</p></div>
              <span className="text-xs text-gray-500">{l.sessions_started} sessions - {fmtDur(l.used_seconds)} used</span>
              <div className="flex gap-2"><CopyButton text={l.url} label="Copy" /><a aria-label="Open guest page" href={l.url} target="_blank" rel="noreferrer" className="rounded-lg border border-white/10 bg-white/5 p-1.5 text-gray-300 hover:text-white"><ExternalLink size={13} /></a></div>
            </div>))}
        </div>
      </>)}
      <h2 className="mb-3 mt-10 text-sm font-medium text-gray-300">History</h2>
      {past === null ? <Skeleton className="h-32" /> : past.length === 0 ? (
        <Empty kind="conversation" title="No conversations yet" hint="Your finished and active conversations will be listed here with the time they used." />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-white/10">
          {past.map((c) => (
            <button key={c.id} onClick={() => setDetail(c)} className="flex w-full items-center gap-4 border-b border-white/5 bg-ink-2/60 px-4 py-3 text-left transition last:border-0 hover:bg-white/[0.05]">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-white/5 text-gray-400"><MessagesSquare size={16} /></span>
              <div className="min-w-0 flex-1"><p className="truncate text-sm">{pname(c.persona_id)}</p><p className="truncate font-mono text-xs text-gray-500">{c.id}</p></div>
              <span className="hidden text-xs text-gray-500 md:block">{fmtDate(c.started_at)}</span><span className="hidden items-center gap-1 text-xs text-gray-500 sm:flex"><Clock size={12} />{fmtDur(c.seconds_used)}</span><Badge s={c.status} />
            </button>
          ))}
        </div>
      )}
      <ConversationDetail conv={detail} personaName={detail ? pname(detail.persona_id) : ""} onClose={() => setDetail(null)} onPlay={(c) => { setCur(c); setDetail(null); window.scrollTo({ top: 0, behavior: "smooth" }); }} />
    </Shell>
  );
}
