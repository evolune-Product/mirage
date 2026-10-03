"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Trash2, Server, Gauge, ScrollText } from "lucide-react";
import { API_URL, api, clearKey, getKey, fmtDate } from "@/lib/api";
import { Shell, ConfirmDialog, Skeleton, Badge, toast } from "@/components/ui";

type Status = { plan: { id: string; name: string; price_cents: number; included_minutes: number; overage_cents_per_min: number }; credits_seconds: number };
type Entry = { id: string; kind: string; seconds: number; created_at: string };
type Audit = { id?: string; action: string; target?: string; detail?: string; created_at: string };

const Row = ({ k, v }: { k: string; v: React.ReactNode }) => <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-white/5 py-2.5 text-sm last:border-0"><dt className="text-gray-400">{k}</dt><dd className="min-w-0 break-all text-right font-mono text-xs text-gray-200">{v}</dd></div>;

export default function Settings() {
  const router = useRouter();
  const [st, setSt] = useState<Status | null>(null); const [ledger, setLedger] = useState<Entry[] | null>(null); const [audit, setAudit] = useState<Audit[] | null>(null);
  const [health, setHealth] = useState<"ok" | "down" | null>(null); const [del, setDel] = useState(false); const [key, setK] = useState("");
  useEffect(() => {
    setK(getKey());
    api<Status>("/v1/billing/status").then(setSt).catch((x) => toast.error(x));
    api<Entry[]>("/v1/usage/ledger?limit=200").then(setLedger).catch(() => setLedger([]));
    api<Audit[]>("/v1/audit?limit=8").then(setAudit).catch(() => setAudit([]));
    fetch(API_URL + "/health").then((r) => setHealth(r.ok ? "ok" : "down")).catch(() => setHealth("down"));
  }, []);
  const used = (ledger ?? []).filter((e) => e.kind === "usage").reduce((a, e) => a + -e.seconds, 0);
  const secs = st?.credits_seconds ?? 0; const incl = (st?.plan.included_minutes ?? 0) * 60;
  const pct = Math.min(100, incl ? (used / incl) * 100 : 0);
  return (
    <Shell title="Settings" subtitle="Your plan, this environment and your data.">
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card">
          <h2 className="mb-4 flex items-center gap-2 font-display text-2xl"><Gauge size={18} className="text-mirage-violet" />Plan and usage</h2>
          {!st ? <Skeleton className="h-32" /> : (<>
            <div className="flex items-center gap-3"><span className="rounded-full bg-mirage-gradient px-3 py-1 text-xs font-semibold uppercase tracking-wide text-white" data-testid="plan-name">{st.plan.name}</span>
              <span className="text-sm text-gray-400">{st.plan.price_cents ? `$${(st.plan.price_cents / 100).toFixed(0)} / month` : "Free"} - {st.plan.included_minutes} minutes included</span></div>
            <p className="mt-5 font-display text-5xl leading-none">{Math.floor(secs / 60)}<span className="text-2xl text-gray-500">m {secs % 60}s</span></p>
            <p className="mt-1 text-xs text-gray-500">credits remaining</p>
            {incl > 0 && <><div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full bg-mirage-gradient" style={{ width: pct + "%" }} /></div>
              <p className="mt-1.5 text-xs text-gray-500">{(used / 60).toFixed(1)} of {st.plan.included_minutes} included minutes used (from the ledger)</p></>}
            <Link href="/dashboard/billing" className="btn-ghost mt-5">Manage billing</Link>
          </>)}
        </section>

        <section className="card">
          <h2 className="mb-2 flex items-center gap-2 font-display text-2xl"><Server size={18} className="text-mirage-cyan" />Environment</h2>
          <dl>
            <Row k="API base URL" v={API_URL} />
            <Row k="API status" v={health === null ? "checking..." : health === "ok" ? <Badge s="ready" /> : <Badge s="error" />} />
            <Row k="Dashboard key" v={key ? key.slice(0, 8) + "..." : "none"} />
            <Row k="Interactive API docs" v={<a className="text-mirage-rose hover:underline" href={API_URL + "/docs"} target="_blank" rel="noreferrer">{API_URL}/docs</a>} />
            <Row k="Dashboard origin" v={typeof window !== "undefined" ? window.location.origin : ""} />
          </dl>
        </section>

        <section className="card lg:col-span-2">
          <h2 className="mb-3 flex items-center gap-2 font-display text-2xl"><ScrollText size={18} className="text-mirage-amber" />Recent account activity</h2>
          {audit === null ? <Skeleton className="h-20" /> : audit.length === 0 ? <p className="text-sm text-gray-500">No activity recorded yet.</p> : (
            <ul className="divide-y divide-white/5 text-sm">{audit.map((a, i) => <li key={a.id ?? i} className="flex flex-wrap items-baseline gap-x-4 py-2"><span className="font-mono text-xs text-gray-200">{a.action}</span><span className="min-w-0 flex-1 truncate text-xs text-gray-500">{a.target} {a.detail}</span><span className="text-xs text-gray-500">{fmtDate(a.created_at)}</span></li>)}</ul>)}
        </section>

        <section className="rounded-2xl border border-mirage-rose/30 bg-mirage-rose/[0.04] p-5 lg:col-span-2">
          <h2 className="font-display text-2xl text-mirage-rose">Delete my data</h2>
          <p className="mt-1 max-w-2xl text-sm text-gray-400">Permanently erases your replicas, consent recordings, personas, knowledge, conversations, transcripts, videos, webhooks and keys. A deletion receipt without personal content is kept. This cannot be undone.</p>
          <button className="mt-4 inline-flex items-center gap-2 rounded-full bg-mirage-rose px-5 py-2.5 text-sm font-semibold text-white hover:brightness-110" onClick={() => setDel(true)}><Trash2 size={15} />Delete my data</button>
        </section>
      </div>
      <ConfirmDialog open={del} typed="delete-my-data" title="Delete all your data?" confirmLabel="Delete everything" onClose={() => setDel(false)}
        body="Everything in this account is erased immediately and you will be signed out. There is no recovery."
        onConfirm={async () => { try { await api("/v1/account/delete-my-data", { body: { confirm: "delete-my-data" } }); clearKey(); toast.success("Your data was deleted."); setDel(false); router.push("/"); } catch (x) { toast.error(x); } }} />
    </Shell>
  );
}
