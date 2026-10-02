"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { Loader2, MessagesSquare, Play, Square, Clock } from "lucide-react";
import { API_URL, api, Conversation, Persona, getKey, saveId } from "@/lib/api";
import { Shell, Badge, Empty, Skeleton, refreshCredits, toast } from "@/components/ui";

export default function Conversations() {
  const [ps, setPs] = useState<Persona[] | null>(null); const [pid, setPid] = useState(""); const [cur, setCur] = useState<Conversation | null>(null);
  const [past, setPast] = useState<Conversation[] | null>(null); const [busy, setBusy] = useState(false);
  const loadPast = () => api<Conversation[]>("/v1/conversations").then((l) => setPast([...l].reverse())).catch((x) => { toast.error(x); setPast([]); });
  useEffect(() => { api<Persona[]>("/v1/personas").then((l) => { setPs(l); if (l[0]) setPid(l[0].id); }).catch((x) => { toast.error(x); setPs([]); }); loadPast(); }, []);
  async function start() {
    setBusy(true);
    try { const c = await api<Conversation>("/v1/conversations", { body: { persona_id: pid } }); saveId("conversations", c.id); setCur(c); loadPast(); }
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
        <div className="card flex flex-wrap items-end gap-3">
          <div className="min-w-60 flex-1"><label className="label">Persona</label>
            <select className="input" value={pid} onChange={(e) => setPid(e.target.value)}>{ps.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
          <button className="btn-grad" disabled={!pid || busy} onClick={start}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}Start conversation</button>
        </div>
      )}
      <AnimatePresence>
        {cur && (
          <motion.div key={cur.id} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="mt-6 overflow-hidden rounded-2xl border border-white/10 bg-ink-2 shadow-[0_30px_80px_-30px_rgba(124,92,255,.35)]">
            <div className="flex items-center justify-between gap-3 border-b border-white/10 bg-ink-3/60 px-4 py-2.5">
              <div className="flex min-w-0 items-center gap-3"><span className="hidden gap-1.5 sm:flex"><i className="h-2.5 w-2.5 rounded-full bg-mirage-rose/70" /><i className="h-2.5 w-2.5 rounded-full bg-mirage-amber/70" /><i className="h-2.5 w-2.5 rounded-full bg-mirage-mint/70" /></span>
                <span className="truncate font-mono text-xs text-gray-400">{cur.id}</span><Badge s={cur.status} />{cur.status === "ended" && <span className="text-xs text-gray-500">{cur.seconds_used}s used</span>}</div>
              {cur.status !== "ended" && <button className="btn-ghost !px-3 !py-1.5 text-xs" onClick={end}><Square size={12} />End</button>}
            </div>
            <iframe src={src} className="h-[620px] w-full bg-ink" allow="camera; microphone; autoplay" title="Playground" />
          </motion.div>
        )}
      </AnimatePresence>
      <h2 className="mb-3 mt-10 text-sm font-medium text-gray-300">History</h2>
      {past === null ? <Skeleton className="h-32" /> : past.length === 0 ? (
        <Empty kind="conversation" title="No conversations yet" hint="Your finished and active conversations will be listed here with the time they used." />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-white/10">
          {past.map((c) => (
            <button key={c.id} onClick={() => setCur(c)} className="flex w-full items-center gap-4 border-b border-white/5 bg-ink-2/60 px-4 py-3 text-left transition last:border-0 hover:bg-white/[0.05]">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-white/5 text-gray-400"><MessagesSquare size={16} /></span>
              <div className="min-w-0 flex-1"><p className="truncate text-sm">{pname(c.persona_id)}</p><p className="truncate font-mono text-xs text-gray-500">{c.id}</p></div>
              <span className="hidden items-center gap-1 text-xs text-gray-500 sm:flex"><Clock size={12} />{c.seconds_used}s</span><Badge s={c.status} />
            </button>
          ))}
        </div>
      )}
    </Shell>
  );
}
