"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Shell, Stat, Empty, Skeleton, toast } from "@/components/ui";
import { BarChart, HBars, StatusBar } from "@/components/charts";

type A = {
  range_days: number; totals: { conversations: number; minutes: number; user_turns: number; agent_turns: number; tool_calls: number; interruptions: number };
  conversations_per_day: { date: string; conversations: number; minutes: number }[];
  first_audio_latency_ms: { avg: number | null; p50: number | null; p95: number | null; samples: number };
  top_personas: { persona_id: string; name: string; conversations: number; minutes: number }[];
  videos: { total: number; by_status: Record<string, number>; per_day: { date: string; videos: number }[] };
  replicas: { total: number; ready: number }; credits_seconds: number;
};
const RANGES = [7, 30, 90];
const VIOLET = "#7c5cff", CYAN = "#5ce1e6", MINT = "#9cf0c4", ROSE = "#ff4d8d", AMBER = "#ff9e5e";
const lat = (v: number | null) => (v == null ? "-" : v >= 1000 ? (v / 1000).toFixed(2) + " s" : Math.round(v) + " ms");

function Card({ title, sub, children, table }: { title: string; sub?: string; children: React.ReactNode; table?: React.ReactNode }) {
  return (
    <div className="card">
      <h2 className="font-display text-2xl">{title}</h2>{sub && <p className="mb-4 text-xs text-gray-500">{sub}</p>}
      <div className={sub ? "" : "mt-4"}>{children}</div>
      {table && <details className="mt-4 text-xs text-gray-400"><summary className="cursor-pointer select-none hover:text-white">View as table</summary><div className="mt-2 max-h-56 overflow-auto rounded-lg border border-white/10">{table}</div></details>}
    </div>
  );
}
const NoData = ({ text }: { text: string }) => <div className="grid h-40 place-items-center rounded-xl border border-dashed border-white/15 text-sm text-gray-500">{text}</div>;

export default function Analytics() {
  const [days, setDays] = useState(30); const [d, setD] = useState<A | null>(null);
  useEffect(() => { let live = true; setD(null); api<A>(`/v1/analytics?days=${days}`).then((r) => live && setD(r)).catch((x) => { toast.error(x); }); return () => { live = false; }; }, [days]);
  const range = (
    <div className="inline-flex rounded-full border border-white/10 bg-white/5 p-0.5" role="tablist" aria-label="Date range">
      {RANGES.map((r) => <button key={r} role="tab" aria-selected={days === r} onClick={() => setDays(r)} className={`rounded-full px-3.5 py-1 text-xs transition ${days === r ? "bg-white text-ink" : "text-gray-400 hover:text-white"}`}>{r}d</button>)}
    </div>
  );
  const series = d?.conversations_per_day ?? [];
  const noConvs = !!d && d.totals.conversations === 0;
  const st = d?.videos.by_status ?? {};
  return (
    <Shell title="Analytics" subtitle="How your agents and videos are being used. Latency is measured from the end of the user's speech to the first audio of the reply." action={range}>
      {!d ? <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><Skeleton className="h-28" /><Skeleton className="h-28" /><Skeleton className="h-28" /><Skeleton className="h-28" /></div> : (<>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" data-testid="stats">
          <Stat label="Conversations" value={d.totals.conversations} sub={`${d.totals.user_turns + d.totals.agent_turns} turns, ${d.totals.interruptions} interruptions`} />
          <Stat label="Minutes talked" value={d.totals.minutes.toFixed(1)} sub={`${Math.floor(d.credits_seconds / 60)} min credits left`} />
          <Stat label="Avg first audio" value={lat(d.first_audio_latency_ms.avg)} sub={d.first_audio_latency_ms.samples ? `p50 ${lat(d.first_audio_latency_ms.p50)} - p95 ${lat(d.first_audio_latency_ms.p95)} - ${d.first_audio_latency_ms.samples} replies` : "No measured replies yet"} />
          <Stat label="Videos" value={d.videos.total} sub={`${d.replicas.ready}/${d.replicas.total} replicas ready`} />
        </div>
        {noConvs && d.videos.total === 0 ? (
          <div className="mt-6"><Empty kind="chart" title="Nothing to chart yet" hint="Talk to a persona or generate a video and the usage charts fill in here." action={<Link href="/dashboard/conversations" className="btn-grad">Start a conversation</Link>} /></div>
        ) : (
          <div className="mt-6 grid gap-4 lg:grid-cols-2">
            <Card title="Conversations per day" sub="Started per day, UTC" table={<DayTable rows={series.map((s) => [s.date, String(s.conversations)])} head={["Day", "Conversations"]} />}>
              {noConvs ? <NoData text="No conversations in this range" /> : <BarChart title="Conversations per day" data={series.map((s) => ({ label: s.date, value: s.conversations }))} color={VIOLET} unit="conv." fmt={(v) => String(Math.round(v * 10) / 10)} />}
            </Card>
            <Card title="Minutes per day" sub="Billed conversation time" table={<DayTable rows={series.map((s) => [s.date, s.minutes.toFixed(2)])} head={["Day", "Minutes"]} />}>
              {noConvs ? <NoData text="No conversation time in this range" /> : <BarChart title="Minutes per day" data={series.map((s) => ({ label: s.date, value: s.minutes }))} color={CYAN} unit="min" />}
            </Card>
            <Card title="Top personas" sub="By conversations in range">
              {d.top_personas.length === 0 ? <NoData text="No persona activity yet" /> : <HBars color={VIOLET} rows={d.top_personas.map((p) => ({ label: p.name, value: p.conversations, sub: `${p.minutes.toFixed(1)} min` }))} />}
            </Card>
            <Card title="Videos" sub="Generated in range, by status">
              {d.videos.total === 0 ? <NoData text="No videos generated in this range" /> : (<>
                <StatusBar parts={[{ label: "ready", value: st.ready ?? 0, color: MINT }, { label: "rendering", value: (st.rendering ?? 0) + (st.training ?? 0), color: CYAN }, { label: "queued", value: st.queued ?? 0, color: VIOLET }, { label: "error", value: st.error ?? 0, color: ROSE }]} />
                {d.videos.per_day.length > 0 && <div className="mt-5"><BarChart height={90} title="Videos per day" data={d.videos.per_day.map((v) => ({ label: v.date, value: v.videos }))} color={AMBER} unit="videos" /></div>}
              </>)}
            </Card>
          </div>)}
      </>)}
    </Shell>
  );
}
function DayTable({ rows, head }: { rows: string[][]; head: string[] }) {
  return <table className="w-full text-left"><thead className="sticky top-0 bg-ink-3 text-[10px] uppercase tracking-wider text-gray-500"><tr>{head.map((h) => <th key={h} className="px-3 py-1.5">{h}</th>)}</tr></thead>
    <tbody>{rows.map((r) => <tr key={r[0]} className="border-t border-white/5">{r.map((c, i) => <td key={i} className="px-3 py-1 font-mono">{c}</td>)}</tr>)}</tbody></table>;
}
