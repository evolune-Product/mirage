"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Shell, Err } from "@/components/ui";

type Plan = { id: string; name: string; price_cents: number; included_minutes: number; overage_cents_per_min: number };
type Topup = { sku: string; minutes: number; price_cents: number };
const usd = (c: number) => `$${(c / 100).toFixed(c % 100 ? 2 : 0)}`;

export default function Billing() {
  const [plans, setPlans] = useState<Plan[]>([]); const [topups, setTopups] = useState<Topup[]>([]);
  const [status, setStatus] = useState<{ plan: Plan; credits_seconds: number } | null>(null);
  const [provider, setProvider] = useState("stripe"); const [err, setErr] = useState("");
  useEffect(() => {
    api<{ plans: Plan[]; topups: Topup[] }>("/v1/billing/plans").then(r => { setPlans(r.plans); setTopups(r.topups); }).catch(x => setErr(x.message));
    api<{ plan: Plan; credits_seconds: number }>("/v1/billing/status").then(setStatus).catch(x => setErr(x.message));
  }, []);
  async function buy(kind: "plan" | "topup", sku: string) {
    setErr("");
    try {
      const r = await api<{ url?: string; checkout_url?: string }>("/v1/billing/checkout", { body: { provider, kind, sku, success_url: location.href, cancel_url: location.href } });
      const u = r.url || r.checkout_url; if (u) location.href = u; else setErr("Checkout returned no URL.");
    } catch (x) { setErr((x as Error).message); }
  }
  return (
    <Shell title="Billing">
      {status && <div className="card mb-6"><p className="label">Current plan</p><p className="text-xl font-semibold">{status.plan.name}</p><p className="text-sm text-gray-400">{Math.floor(status.credits_seconds / 60)} min of credit remaining</p></div>}
      <div className="mb-4 flex items-center gap-3"><span className="label !mb-0">Pay with</span>
        <select className="input !w-40" value={provider} onChange={e => setProvider(e.target.value)}><option value="stripe">Stripe</option><option value="razorpay">Razorpay</option></select></div>
      <h2 className="mb-3 font-medium">Plans</h2>
      <div className="mb-8 grid gap-3 md:grid-cols-3">{plans.filter(p => p.price_cents > 0).map(p =>
        <div key={p.id} className="card"><p className="font-medium">{p.name}</p><p className="text-2xl font-semibold">{usd(p.price_cents)}<span className="text-sm text-gray-400"> / month</span></p>
          <p className="text-sm text-gray-400">{p.included_minutes} min included, then {usd(p.overage_cents_per_min)}/min</p>
          <button className="btn mt-3 w-full" onClick={() => buy("plan", p.id)}>Buy {p.name}</button></div>)}</div>
      <h2 className="mb-3 font-medium">Top-ups</h2>
      <div className="grid gap-3 md:grid-cols-3">{topups.map(t =>
        <div key={t.sku} className="card"><p className="font-medium">{t.minutes} minutes</p><p className="text-xl font-semibold">{usd(t.price_cents)}</p>
          <button className="btn mt-3 w-full" onClick={() => buy("topup", t.sku)}>Buy</button></div>)}</div>
      <Err m={err} />
    </Shell>
  );
}
