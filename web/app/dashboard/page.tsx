"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { ArrowRight, Check, Clapperboard, CreditCard, MessagesSquare, ScanFace, UserRound, BookOpen, ShieldCheck } from "lucide-react";
import { api, Persona, Replica, Conversation, Video } from "@/lib/api";
import { Shell, Skeleton, toast } from "@/components/ui";

type Status = { plan: { name: string; included_minutes: number }; credits_seconds: number };
type Data = { reps: Replica[]; pers: Persona[]; convs: Conversation[]; vids: Video[]; kdocs: number; st: Status | null };

export default function Overview() {
  const [d, setD] = useState<Data | null>(null);
  useEffect(() => { (async () => {
    try {
      const [reps, pers, convs, vids, st] = await Promise.all([
        api<Replica[]>("/v1/replicas"), api<Persona[]>("/v1/personas"), api<Conversation[]>("/v1/conversations"), api<Video[]>("/v1/videos"),
        api<Status>("/v1/billing/status").catch(() => null),
      ]);
      let kdocs = 0;
      await Promise.all(reps.map(async (r) => { if (r.status !== "awaiting_consent") return; try { if ((await api<{ has_consent: boolean }>(`/v1/replicas/${r.id}/consent`)).has_consent) r.status = "training"; } catch {} }));
      await Promise.all(pers.map(async (p) => { try { kdocs += (await api<unknown[]>(`/v1/personas/${p.id}/knowledge`)).length; } catch {} }));
      setD({ reps, pers, convs, vids, kdocs, st });
    } catch (x) { toast.error(x); setD({ reps: [], pers: [], convs: [], vids: [], kdocs: 0, st: null }); }
  })(); }, []);

  const steps = d && [
    { t: "Create a replica", s: "Upload a short training video of the face you want to bring to life.", done: d.reps.length > 0, href: "/dashboard/replicas", cta: "Create replica", icon: ScanFace, n: `${d.reps.length} created` },
    { t: "Give consent", s: "The person on camera confirms by reading a one-time phrase. Required before training.", done: d.reps.some((r) => r.status !== "awaiting_consent"), href: "/dashboard/replicas", cta: "Give consent", icon: ShieldCheck, n: `${d.reps.filter((r) => r.status !== "awaiting_consent").length} consented` },
    { t: "Create a persona with knowledge", s: "Give it a personality and documents it can answer from.", done: d.pers.length > 0 && d.kdocs > 0, href: "/dashboard/personas", cta: "Create persona", icon: BookOpen, n: `${d.pers.length} personas, ${d.kdocs} docs` },
    { t: "Start a conversation", s: "Talk to your persona live, face to face, in the browser.", done: d.convs.length > 0, href: "/dashboard/conversations", cta: "Start talking", icon: MessagesSquare, n: `${d.convs.length} so far` },
    { t: "Generate a video", s: "Turn a script into a talking-head video with your replica.", done: d.vids.length > 0, href: "/dashboard/videos", cta: "Write a script", icon: Clapperboard, n: `${d.vids.length} so far` },
  ];
  const doneN = steps ? steps.filter((s) => s.done).length : 0;
  const next = steps ? steps.findIndex((s) => !s.done) : -1;
  const secs = d?.st?.credits_seconds ?? 0;
  const total = Math.max(secs, (d?.st?.plan.included_minutes ?? 0) * 60, 600);
  const R = 26, C = 2 * Math.PI * R;

  return (
    <Shell title="Overview" subtitle="Your path from zero to a talking AI face."
      action={d && next >= 0 ? <Link href={steps![next].href} className="btn-grad">{steps![next].cta}<ArrowRight size={15} /></Link> : <Link href="/dashboard/conversations" className="btn-grad">New conversation<ArrowRight size={15} /></Link>}>
      <div className="grid gap-5 lg:grid-cols-3">
        <div className="card lg:col-span-2 !p-0 overflow-hidden">
          <div className="flex items-center gap-4 border-b border-white/10 p-5">
            <svg width="64" height="64" viewBox="0 0 64 64" className="shrink-0 -rotate-90"><defs><linearGradient id="ring" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#ff9e5e" /><stop offset=".5" stopColor="#ff4d8d" /><stop offset="1" stopColor="#7c5cff" /></linearGradient></defs>
              <circle cx="32" cy="32" r={R} stroke="rgba(255,255,255,.08)" strokeWidth="5" fill="none" />
              <motion.circle cx="32" cy="32" r={R} stroke="url(#ring)" strokeWidth="5" fill="none" strokeLinecap="round" strokeDasharray={C} initial={{ strokeDashoffset: C }} animate={{ strokeDashoffset: C * (1 - doneN / 5) }} transition={{ duration: 0.9, ease: "easeOut" }} /></svg>
            <div><h2 className="text-lg font-medium">Get started</h2><p className="text-sm text-gray-400">{d ? (doneN === 5 ? "All set. You have used every part of Mirage." : `${doneN} of 5 steps complete`) : "Loading your progress..."}</p></div>
          </div>
          {!steps ? <div className="space-y-3 p-5">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-14" />)}</div> : (
            <ol>{steps.map((s, i) => {
              const cur = i === next; const I = s.icon;
              return (
                <motion.li key={s.t} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.06 }}
                  className={`flex items-center gap-4 border-b border-white/5 p-4 pl-5 last:border-0 ${cur ? "bg-white/[0.04]" : ""}`}>
                  <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full text-xs font-semibold ${s.done ? "bg-mirage-mint text-ink" : cur ? "bg-mirage-gradient text-white" : "bg-white/10 text-gray-400"}`}>{s.done ? <Check size={16} /> : i + 1}</span>
                  <div className="min-w-0 flex-1">
                    <p className={`text-sm font-medium ${s.done ? "text-gray-400 line-through decoration-white/20" : ""}`}>{s.t}</p>
                    <p className="text-xs text-gray-500">{cur ? s.s : s.n}</p>
                  </div>
                  {cur ? <Link href={s.href} className="btn !px-4 !py-1.5 text-xs">{s.cta}</Link> : <I size={16} className="shrink-0 text-gray-600" />}
                </motion.li>
              );
            })}</ol>
          )}
        </div>
        <div className="space-y-5">
          <div className="card">
            <div className="flex items-center justify-between"><p className="label !mb-0">Credits</p><span className="rounded-full bg-mirage-gradient px-2 py-0.5 text-[10px] font-semibold uppercase text-white">{d?.st?.plan.name ?? "..."}</span></div>
            {d ? <><p className="mt-3 font-display text-5xl">{Math.floor(secs / 60)}<span className="text-2xl text-gray-400"> min</span></p>
              <p className="text-xs text-gray-500">{secs} seconds remaining</p>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/10"><motion.div className="h-full rounded-full bg-mirage-gradient" initial={{ width: 0 }} animate={{ width: Math.min(100, (secs / total) * 100) + "%" }} transition={{ duration: 0.9 }} /></div>
              <Link href="/dashboard/billing" className="btn-ghost mt-4 w-full"><CreditCard size={15} />Top up</Link></> : <Skeleton className="mt-3 h-24" />}
          </div>
          <div className="card">
            <p className="label">Quick actions</p>
            <div className="grid gap-2">
              {[["/dashboard/conversations", "Talk to a persona", MessagesSquare], ["/dashboard/videos", "Generate a video", Clapperboard], ["/dashboard/keys", "Copy API snippets", BookOpen]].map(([h, l, I]) => {
                const Ico = I as typeof BookOpen;
                return <Link key={h as string} href={h as string} className="group flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-3.5 py-2.5 text-sm transition hover:border-white/20 hover:bg-white/[0.07]"><Ico size={16} className="text-mirage-rose" />{l as string}<ArrowRight size={14} className="ml-auto text-gray-500 transition group-hover:translate-x-0.5 group-hover:text-white" /></Link>;
              })}
            </div>
          </div>
        </div>
      </div>
    </Shell>
  );
}
