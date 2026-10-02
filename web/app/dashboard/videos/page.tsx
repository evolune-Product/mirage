"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { Clapperboard, Cpu, ExternalLink, Loader2, Sparkles } from "lucide-react";
import { API_URL, api, Replica, Video, saveId } from "@/lib/api";
import { Shell, Badge, Empty, Skeleton, CopyButton, refreshCredits, toast } from "@/components/ui";

const fileUrl = (u: string) => (u.startsWith("http") ? u : API_URL + u);
const MAX = 1000;

export default function Videos() {
  const [reps, setReps] = useState<Replica[] | null>(null); const [rid, setRid] = useState(""); const [script, setScript] = useState(""); const [vids, setVids] = useState<Video[] | null>(null); const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { try { setVids([...(await api<Video[]>("/v1/videos"))].reverse()); } catch { setVids((v) => v ?? []); } }, []);
  useEffect(() => {
    api<Replica[]>("/v1/replicas").then((l) => { const ok = l.filter((r) => r.status === "ready"); setReps(ok); if (ok[0]) setRid(ok[0].id); }).catch((x) => { toast.error(x); setReps([]); });
    load(); const t = setInterval(load, 5000); return () => clearInterval(t);
  }, [load]);
  async function create(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try { const v = await api<Video>("/v1/videos", { body: { replica_id: rid, script } }); saveId("videos", v.id); setScript(""); toast.success("Video queued."); load(); refreshCredits(); }
    catch (x) { toast.error(x); } finally { setBusy(false); }
  }
  const rname = (id: string) => reps?.find((r) => r.id === id)?.name ?? id;
  const over = false;

  return (
    <Shell title="Videos" subtitle="Write a script, pick a replica, get a talking-head video.">
      {reps !== null && reps.length === 0 ? (
        <Empty kind="video" title="No ready replica yet" hint="Videos need a replica that has consent and finished training." action={<Link href="/dashboard/replicas" className="btn-grad">Go to replicas</Link>} />
      ) : (
        <form onSubmit={create} className="card space-y-4">
          <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
            <div><label className="label">Replica (ready only)</label>
              <select className="input" value={rid} onChange={(e) => setRid(e.target.value)}>{(reps ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></div>
            <div>
              <div className="mb-1.5 flex items-center justify-between"><label className="label !mb-0">Script</label><span className={`font-mono text-xs ${over ? "text-mirage-rose" : "text-gray-500"}`}>{script.length} chars - about {Math.max(0, Math.round(script.length / 15))}s of speech</span></div>
              <textarea className="input h-32" required placeholder="Hi, I'm... Today I want to show you..." value={script} onChange={(e) => setScript(e.target.value)} />
              <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/10"><div className={`h-full transition-all ${over ? "bg-mirage-rose" : "bg-mirage-gradient"}`} style={{ width: Math.min(100, (script.length / MAX) * 100) + "%" }} /></div>
            </div>
          </div>
          <button className="btn-grad" disabled={!rid || busy || !script.trim()}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}Generate video</button>
        </form>
      )}
      <h2 className="mb-3 mt-10 text-sm font-medium text-gray-300">Your videos</h2>
      {vids === null ? <div className="grid gap-4 md:grid-cols-2"><Skeleton className="h-64" /><Skeleton className="h-64" /></div> : vids.length === 0 ? (
        <Empty kind="video" title="No videos yet" hint="Generated videos show up here with an inline player as soon as they finish rendering." />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {vids.map((v, i) => (
            <motion.div key={v.id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }} className="card flex flex-col gap-3 !p-4">
              {v.output_url ? <video className="aspect-video w-full rounded-xl bg-black" controls preload="metadata" src={fileUrl(v.output_url)} />
                : <div className="grid aspect-video w-full place-items-center rounded-xl border border-white/10 bg-[radial-gradient(circle_at_50%_40%,rgba(124,92,255,.2),transparent_70%)] text-gray-500"><Clapperboard size={32} /></div>}
              <div className="flex items-center justify-between gap-2"><p className="truncate font-mono text-xs text-gray-500">{v.id} - {rname(v.replica_id)}</p><Badge s={v.status} /></div>
              <p className="line-clamp-2 text-sm text-gray-300">{v.script}</p>
              {v.status === "queued" && <p className="flex items-start gap-2 rounded-xl border border-mirage-violet/25 bg-mirage-violet/10 p-3 text-xs leading-relaxed text-gray-300"><Cpu size={14} className="mt-0.5 shrink-0 text-mirage-violet" /><span>Queued: rendering needs a worker, a separate background process. Run this in the backend folder:<code className="mt-1.5 block rounded bg-black/40 px-2 py-1.5 font-mono text-[11px] text-gray-200">python workers/run_worker.py</code></span></p>}
              {v.status === "rendering" && <p className="text-xs text-mirage-cyan">Rendering now. This page refreshes automatically.</p>}
              {v.output_url && <div className="flex gap-2"><a className="btn-ghost !px-3 !py-1.5 text-xs" href={fileUrl(v.output_url)} target="_blank" rel="noreferrer"><ExternalLink size={13} />Open in new tab</a><CopyButton text={fileUrl(v.output_url)} label="Copy link" /></div>}
            </motion.div>
          ))}
        </div>
      )}
    </Shell>
  );
}
