"use client";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Check, Loader2, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { Shell, Empty, Skeleton, toast } from "@/components/ui";

type Plan = { id: string; name: string; price_cents: number; included_minutes: number; overage_cents_per_min: number };
type Topup = { sku: string; minutes: number; price_cents: number };
type Entry = { id: string; kind: string; seconds: number; amount_cents: number; currency: string; note: string; created_at: string };
const usd = (c: number) => `$${(c / 100).toFixed(c % 100 ? 2 : 0)}`;
const KIND: Record<string, string> = { usage: "text-gray-300 bg-white/5", grant: "text-mirage-mint bg-mirage-mint/10", topup: "text-mirage-cyan bg-mirage-cyan/10", plan: "text-mirage-violet bg-mirage-violet/15", adjust: "text-mirage-amber bg-mirage-amber/10" };

export default function Billing() {
  const [plans, setPlans] = useState<Plan[] | null>(null); const [topups, setTopups] = useState<Topup[]>([]);
  const [status, setStatus] = useState<{ plan: Plan; credits_seconds: number } | null>(null);
  const [ledger, setLedger] = useState<Entry[] | null>(null);
  const [provider, setProvider] = useState("stripe"); const [busy, setBusy] = useState("");
  useEffect(() => {
    api<{ plans: Plan[]; topups: Topup[] }>("/v1/billing/plans").then((r) => { setPlans(r.plans); setTopups(r.topups); }).catch((x) => { toast.error(x); setPlans([]); });
    api<{ plan: Plan; credits_seconds: number }>("/v1/billing/status").then(setStatus).catch((x) => toast.error(x));
    api<Entry[]>("/v1/usage/ledger").then(setLedger).catch((x) => { toast.error(x); setLedger([]); });
  }, []);
  async function buy(kind: "plan" | "topup", sku: string) {
    setBusy(sku);
    try {
      const r = await api<{ url?: string; checkout_url?: string }>("/v1/billing/checkout", { body: { provider, kind, sku, success_url: location.href, cancel_url: location.href } });
      const u = r.url || r.checkout_url; if (u) location.href = u; else toast.error("Checkout returned no URL.");
    } catch (x) { toast.error(x); } finally { setBusy(""); }
  }
  const paid = plans?.filter((p) => p.price_cents > 0) ?? [];
  const mid = Math.floor(paid.length / 2);

  return (
    <Shell title="Billing" subtitle="Plans include minutes; top-ups never expire. Every second used is logged below."
      action={<div className="flex items-center gap-2"><span className="label !mb-0">Pay with</span><select className="input !w-32" value={provider} onChange={(e) => setProvider(e.target.value)}><option value="stripe">Stripe</option><option value="razorpay">Razorpay</option></select></div>}>
      {status ? (
        <div className="card mb-10 flex flex-wrap items-center justify-between gap-4 bg-[radial-gradient(30rem_12rem_at_0%_0%,rgba(255,77,141,.12),transparent)]">
          <div><p className="label">Current plan</p><p className="font-display text-4xl">{status.plan.name}</p></div>
          <div className="text-right"><p className="font-display text-4xl">{Math.floor(status.credits_seconds / 60)}<span className="text-xl text-gray-400"> min</span></p><p className="text-sm text-gray-400">of credit remaining</p></div>
        </div>
      ) : <Skeleton className="mb-10 h-28" />}

      <h2 className="mb-3 font-medium">Plans</h2>
      <div className="mb-10 grid gap-4 md:grid-cols-3">
        {plans === null ? [0, 1, 2].map((i) => <Skeleton key={i} className="h-52" />) : paid.map((p, i) => {
          const cur = status?.plan.id === p.id; const pop = i === mid && paid.length > 2;
          return (
            <motion.div key={p.id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.06 }} className={`card relative flex flex-col ${pop ? "!border-mirage-rose/50 shadow-[0_0_50px_-15px_rgba(255,77,141,.5)]" : ""}`}>
              {pop && <span className="absolute -top-2.5 right-4 rounded-full bg-mirage-gradient px-2.5 py-0.5 text-[10px] font-semibold uppercase text-white">Popular</span>}
              <p className="font-medium">{p.name}</p>
              <p className="mt-2 font-display text-5xl">{usd(p.price_cents)}<span className="font-sans text-sm text-gray-400"> / month</span></p>
              <p className="mt-3 flex items-center gap-2 text-sm text-gray-300"><Check size={14} className="text-mirage-mint" />{p.included_minutes} minutes included</p>
              <p className="mt-1 flex items-center gap-2 text-sm text-gray-400"><Check size={14} className="text-mirage-mint" />then {usd(p.overage_cents_per_min)}/min</p>
              <button className={`${pop ? "btn-grad" : "btn"} mt-5 w-full`} disabled={!!busy || cur} onClick={() => buy("plan", p.id)}>{busy === p.id && <Loader2 size={14} className="animate-spin" />}{cur ? "Current plan" : `Buy ${p.name}`}</button>
            </motion.div>
          );
        })}
      </div>

      <h2 className="mb-3 font-medium">Top-ups</h2>
      <div className="mb-10 grid gap-4 sm:grid-cols-2 md:grid-cols-3">
        {topups.map((t) => (
          <div key={t.sku} className="card flex items-center justify-between gap-3">
            <div><p className="font-medium">{t.minutes} minutes</p><p className="font-display text-3xl">{usd(t.price_cents)}</p></div>
            <button className="btn" disabled={!!busy} onClick={() => buy("topup", t.sku)}>{busy === t.sku ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}Buy</button>
          </div>
        ))}
      </div>

      <h2 className="mb-3 font-medium">Usage ledger</h2>
      {ledger === null ? <Skeleton className="h-40" /> : ledger.length === 0 ? <Empty kind="ledger" title="Nothing here yet" hint="Credits granted, purchased and spent will be listed here." /> : (
        <div className="overflow-x-auto rounded-2xl border border-white/10">
          <table className="w-full min-w-[520px] text-sm">
            <thead className="bg-white/[0.03] text-left text-xs uppercase tracking-wider text-gray-500"><tr><th className="px-4 py-3 font-medium">When</th><th className="px-4 py-3 font-medium">Type</th><th className="px-4 py-3 font-medium">Note</th><th className="px-4 py-3 text-right font-medium">Seconds</th></tr></thead>
            <tbody>{ledger.map((e) => (
              <tr key={e.id} className="border-t border-white/5 hover:bg-white/[0.03]">
                <td className="whitespace-nowrap px-4 py-2.5 text-gray-400">{new Date(e.created_at + (e.created_at.endsWith("Z") ? "" : "Z")).toLocaleString()}</td>
                <td className="px-4 py-2.5"><span className={`rounded-full px-2 py-0.5 text-xs ${KIND[e.kind] || KIND.usage}`}>{e.kind}</span></td>
                <td className="px-4 py-2.5 text-gray-400">{e.note || "-"}</td>
                <td className={`px-4 py-2.5 text-right font-mono ${e.seconds > 0 ? "text-mirage-mint" : "text-gray-300"}`}>{e.seconds > 0 ? "+" : ""}{e.seconds}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </Shell>
  );
}
