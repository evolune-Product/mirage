"use client";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Captions, Clapperboard, Cpu, Download, ExternalLink, ImageIcon, Check } from "lucide-react";
import { api, fileUrl, signedUrl, Video } from "@/lib/api";
import { Badge, CopyButton, Spinner } from "@/components/ui";
import { Progress } from "@/components/kit";

export type Creative = { video_id: string; status: string; options: { format?: string; resolution?: number; captions?: { style: string }; background?: { type: string } | null; logo?: unknown; scenes?: string; transition?: string } | null; progress: { stage: string; scene?: number; scenes?: number; percent?: number } | null; thumbnail_url: string | null; captions_url: string | null; output_url: string | null };
type Job = { error: string | null; attempts: number };
const ASPECT: Record<string, string> = { "16:9": "aspect-video", "9:16": "aspect-[9/16] max-w-[15rem] mx-auto", "1:1": "aspect-square max-w-[22rem] mx-auto" };

export function Stages({ c }: { c: Creative }) {
  const p = c.progress; if (!p || p.stage === "done") return null;
  const n = p.scenes ?? 1; const cur = p.scene ?? 0; const tts = p.stage === "tts";
  return (
    <div className="rounded-xl border border-vocalface-cyan/20 bg-vocalface-cyan/[0.05] p-3" data-testid="video-progress">
      <p className="flex items-center gap-2 text-xs text-vocalface-cyan"><Spinner size={13} />{tts ? "Generating the voice and caption timings" : `Rendering scene ${cur || 1} of ${n}`}</p>
      <Progress className="mt-2" pct={p.percent ?? (tts ? 5 : 20)} label={`${Math.round(p.percent ?? 0)}%`} />
      {n > 1 && <ol className="mt-1 flex flex-wrap gap-1.5" aria-label="Scene progress">{Array.from({ length: n }).map((_, i) => { const done = !tts && i + 1 < cur; const on = !tts && i + 1 === cur; return (
        <li key={i} className={`grid h-6 min-w-6 place-items-center rounded-full px-1.5 text-[10px] ring-1 ring-inset ${done ? "bg-vocalface-mint text-ink ring-vocalface-mint" : on ? "bg-vocalface-violet/25 text-white ring-vocalface-violet animate-pulse" : "text-gray-500 ring-white/15"}`}>{done ? <Check size={11} /> : i + 1}</li>); })}</ol>}
    </div>
  );
}

export default function VideoCard({ v, rname, extra, i = 0 }: { v: Video; rname: string; extra?: React.ReactNode; i?: number }) {
  const [c, setC] = useState<Creative | null>(null); const [job, setJob] = useState<Job | null>(null); const [thumb, setThumb] = useState(""); const [srt, setSrt] = useState(""); const [mp4, setMp4] = useState("");
  const live = v.status === "queued" || v.status === "rendering";
  useEffect(() => {
    let on = true; const load = () => api<Creative>(`/v1/videos/${v.id}/creative`).then((x) => on && setC(x)).catch(() => {});
    load(); if (!live) return () => { on = false; }; const t = setInterval(load, 3000); return () => { on = false; clearInterval(t); };
  }, [v.id, live, v.status]);
  useEffect(() => { if (v.status === "error") api<Job>(`/v1/jobs/video/${v.id}`).then(setJob).catch(() => {}); }, [v.id, v.status]);
  useEffect(() => { if (c?.thumbnail_url && v.status === "ready") signedUrl(c.thumbnail_url).then(setThumb).catch(() => {}); if (c?.captions_url) signedUrl(c.captions_url).then(setSrt).catch(() => {}); }, [c?.thumbnail_url, c?.captions_url, v.status]);
  useEffect(() => { if (v.output_url) setMp4(fileUrl(v.output_url)); }, [v.output_url]);
  const fmt = c?.options?.format ?? "16:9"; const o = c?.options;
  const chips = o ? [o.format, o.resolution ? `${o.resolution}p` : "", o.captions ? `${o.captions.style} captions` : "", o.background?.type ? `${o.background.type} bg` : "", o.logo ? "logo" : ""].filter(Boolean) : [];
  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i, 8) * 0.04 }} className="card flex flex-col gap-3 !p-4" data-testid="video-card">
      {mp4 ? <video className={`w-full rounded-xl bg-black ${ASPECT[fmt]}`} controls preload="metadata" poster={thumb || undefined} src={mp4} />
        : <div className={`grid w-full place-items-center rounded-xl border border-white/10 bg-[radial-gradient(circle_at_50%_40%,rgba(124,92,255,.2),transparent_70%)] text-gray-500 ${ASPECT[fmt]}`}><Clapperboard size={32} /></div>}
      <div className="flex items-center justify-between gap-2"><p className="truncate font-mono text-xs text-gray-500">{v.id} - {rname}</p><Badge s={v.status} /></div>
      {extra}
      {chips.length > 0 && <div className="flex flex-wrap gap-1.5 text-[11px]">{chips.map((x) => <span key={x} className="rounded-full bg-white/5 px-2 py-0.5 text-gray-300">{x}</span>)}</div>}
      <p className="line-clamp-2 text-sm text-gray-300">{v.script}</p>
      {v.status === "queued" && <p className="flex items-start gap-2 rounded-xl border border-vocalface-violet/25 bg-vocalface-violet/10 p-3 text-xs leading-relaxed text-gray-300"><Cpu size={14} className="mt-0.5 shrink-0 text-vocalface-violet" /><span>Queued: rendering needs a worker, a separate background process. Run this in the backend folder:<code className="mt-1.5 block rounded bg-black/40 px-2 py-1.5 font-mono text-[11px] text-gray-200">python workers/run_worker.py</code></span></p>}
      {live && c && <Stages c={c} />}
      {v.status === "rendering" && !c?.progress && <p className="text-xs text-vocalface-cyan">Rendering now. This card refreshes automatically.</p>}
      {v.status === "error" && <div className="rounded-xl bg-vocalface-rose/10 p-3 text-xs text-vocalface-rose" role="alert" data-testid="video-error"><p className="font-medium">Rendering failed{job?.attempts ? ` after ${job.attempts} attempt${job.attempts === 1 ? "" : "s"}` : ""}.</p><p className="mt-1 break-words text-gray-300">{job?.error ? job.error.slice(0, 300) : "No error text was recorded. Check the worker logs, then try again."}</p><p className="mt-1 text-gray-400">Your minutes are refunded when a render fails.</p></div>}
      {v.status === "ready" && (
        <div className="flex flex-wrap gap-2">
          {mp4 && <a className="btn-ghost !px-3 !py-1.5 text-xs" href={mp4} target="_blank" rel="noreferrer"><ExternalLink size={13} />Open</a>}
          {mp4 && <a className="btn-ghost !px-3 !py-1.5 text-xs" href={mp4} download><Download size={13} />MP4</a>}
          {thumb && <a className="btn-ghost !px-3 !py-1.5 text-xs" href={thumb} target="_blank" rel="noreferrer" data-testid="thumb-link"><ImageIcon size={13} />Thumbnail</a>}
          {srt && <a className="btn-ghost !px-3 !py-1.5 text-xs" href={srt} target="_blank" rel="noreferrer" download="captions.srt" data-testid="srt-link"><Captions size={13} />SRT</a>}
          {mp4 && <CopyButton text={mp4} label="Copy link" />}
        </div>)}
    </motion.div>
  );
}
