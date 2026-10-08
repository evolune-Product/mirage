"use client";
import { useCallback, useEffect, useState } from "react";
import { Gauge, Save } from "lucide-react";
import { api, errText } from "@/lib/api";
import { Section, Skeleton, Spinner, Stat, Toggle, toast } from "@/components/ui";
import { Callout, Progress } from "@/components/kit";
import { BarChart, HBars } from "@/components/charts";

type Status = { plan: { id: string; name: string; included_minutes: number }; credits_seconds: number; period: string; overage: { enabled: boolean; cap_cents: number | null; headroom_seconds: number }; available_seconds: number };
type Over = { enabled: boolean; cap_cents: number | null; rate_cents_per_min: number; available: boolean; spent_cents: number; headroom_seconds: number; period: string };
type Report = { period: string; plan: { name: string; included_minutes: number; overage_cents_per_min: number }; credits_seconds: number; granted_seconds: number; used_seconds: number; used_seconds_by_kind: Record<string, number>; daily_used_seconds: { date: string; seconds: number }[]; overage: { enabled: boolean; cap_cents: number | null; rate_cents_per_min: number; seconds: number; spent_cents: number; remaining_cents: number } };
const mins = (s: number) => `${(s / 60).toFixed(s % 60 ? 1 : 0)} min`;
const usd = (c: number) => `$${(c / 100).toFixed(2)}`;
const periods = () => Array.from({ length: 6 }).map((_, i) => { const d = new Date(); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - i); return d.toISOString().slice(0, 7); });

export default function UsagePanel({ onForbidden }: { onForbidden: () => void }) {
  const [st, setSt] = useState<Status | null>(null); const [ov, setOv] = useState<Over | null>(null); const [rep, setRep] = useState<Report | null>(null); const [period, setPeriod] = useState(periods()[0]);
  const [en, setEn] = useState(false); const [cap, setCap] = useState(""); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const loadAll = useCallback(() => {
    api<Status>("/v1/billing/status").then(setSt).catch(() => {});
    api<Over>("/v1/billing/overage").then((o) => { setOv(o); setEn(o.enabled); setCap(o.cap_cents ? String(o.cap_cents / 100) : ""); }).catch((x: Error) => { if (/^403/.test(x.message)) onForbidden(); });
  }, [onForbidden]);
  useEffect(() => { loadAll(); }, [loadAll]);
  useEffect(() => { setRep(null); api<Report>(`/v1/usage/report?period=${period}`).then(setRep).catch((x) => { toast.error(x); }); }, [period]);
  async function saveOv() {
    setBusy(true); setErr("");
    try {
      const cents = Math.round(parseFloat(cap || "0") * 100);
      if (en && !(cents > 0)) throw new Error("Set a monthly spend cap above $0 to turn overage on. It stops extra usage when the cap is reached.");
      const o = await api<Over>("/v1/billing/overage", { method: "PUT", body: { enabled: en, cap_cents: en ? cents : undefined } });
      setOv(o); toast.success(en ? "Overage on. Usage beyond your minutes is billed up to the cap." : "Overage off."); loadAll();
    } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  const total = Math.max((rep?.granted_seconds ?? 0), (rep?.used_seconds ?? 0), 1);
  const usedPct = rep ? (rep.used_seconds / total) * 100 : 0;
  const kinds = rep ? [["Conversations", rep.used_seconds_by_kind.conversation ?? 0], ["Videos", rep.used_seconds_by_kind.video ?? 0], ["Other", rep.used_seconds_by_kind.other ?? 0]].filter(([, v]) => (v as number) > 0) : [];
  return (
    <div className="mb-10 space-y-8">
      <div className="grid gap-4 md:grid-cols-3" data-testid="allowance">
        {st ? (<>
          <Stat label="Available now" value={<>{Math.floor(st.available_seconds / 60)}<span className="text-xl text-gray-400"> min</span></>} sub={st.overage.enabled ? `includes up to ${mins(st.overage.headroom_seconds)} of overage headroom` : "credits only; overage is off"} />
          <Stat label="Credit balance" value={<>{Math.floor(st.credits_seconds / 60)}<span className="text-xl text-gray-400"> min</span></>} sub={`${st.plan.name} plan - ${st.plan.included_minutes} min included per month`} />
          <div className="card !p-4"><p className="label !mb-2">This month ({st.period})</p>
            {rep ? <><p className="font-display text-4xl leading-none">{Math.round(rep.used_seconds / 60)}<span className="text-xl text-gray-400"> min used</span></p>
              <Progress className="mt-3" pct={rep.granted_seconds ? usedPct : 0} tone={usedPct > 90 ? "rose" : "grad"} label={rep.granted_seconds ? `${mins(rep.used_seconds)} of ${mins(rep.granted_seconds)} granted this month` : `${mins(rep.used_seconds)} used; no plan minutes were granted in this month`} /></> : <Skeleton className="h-16" />}</div>
        </>) : [0, 1, 2].map((i) => <Skeleton key={i} className="h-28" />)}
      </div>

      <Section title="Overage" icon={<Gauge size={16} className="text-vocalface-amber" />} hint="When your minutes run out, keep going instead of stopping, billed per minute up to a monthly cap you set. Without it, conversations and videos stop at zero.">
        {!ov ? <Skeleton className="h-28" /> : !ov.available ? <Callout tone="warn" title="Not available on your plan">Overage is for paid plans. Buy a plan below, or top up minutes (they never expire).</Callout> : (
          <div className="card space-y-4 !p-4">
            <Toggle checked={en} onChange={setEn} label="Allow usage beyond my included minutes" hint={`${usd(ov.rate_cents_per_min)} per extra minute. VocalFace records and caps it; it is not charged to a card automatically yet.`} />
            <div className="max-w-xs"><label className="label">Monthly spend cap (USD)</label><div className="relative"><span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-500">$</span><input className="input !pl-7" type="number" min={1} step={1} inputMode="decimal" placeholder="e.g. 25" aria-label="Monthly spend cap" disabled={!en} value={cap} onChange={(e) => setCap(e.target.value)} /></div></div>
            {ov.enabled && ov.cap_cents ? <div><Progress pct={(ov.spent_cents / ov.cap_cents) * 100} tone={ov.spent_cents / ov.cap_cents > 0.9 ? "rose" : "grad"} label={`${usd(ov.spent_cents)} of ${usd(ov.cap_cents)} spent this month (${ov.period}); about ${mins(ov.headroom_seconds)} of headroom left`} /></div> : null}
            {err && <Callout tone="bad">{err}</Callout>}
            <button type="button" className="btn" disabled={busy || (en === ov.enabled && (!en || Math.round(parseFloat(cap || "0") * 100) === ov.cap_cents))} onClick={saveOv}>{busy ? <Spinner size={14} /> : <Save size={14} />}Save overage settings</button>
          </div>)}
      </Section>

      <Section title="Usage report" hint="Seconds used per day and where they went, for one calendar month (UTC)."
        action={<select className="input !w-32 !py-1.5 text-sm" aria-label="Report month" value={period} onChange={(e) => setPeriod(e.target.value)}>{periods().map((p) => <option key={p}>{p}</option>)}</select>}>
        {!rep ? <Skeleton className="h-56" /> : rep.used_seconds === 0 && rep.overage.seconds === 0 ? <p className="rounded-xl border border-dashed border-white/15 p-5 text-sm text-gray-500" data-testid="report-empty">No usage recorded in {rep.period}.</p> : (
          <div className="grid gap-4 lg:grid-cols-3" data-testid="report">
            <div className="card lg:col-span-2"><p className="label">Minutes used per day</p>
              <BarChart title="Minutes used per day" data={rep.daily_used_seconds.map((d) => ({ label: d.date, value: d.seconds / 60 }))} color="#4f6fa8" unit="min" fmt={(v) => String(+v.toFixed(1))} /></div>
            <div className="space-y-4"><div className="card"><p className="label">By kind</p><HBars rows={kinds.map(([l, v]) => ({ label: l as string, value: (v as number) / 60 }))} color="#22d3ee" fmt={(v) => `${+v.toFixed(1)} min`} /></div>
              <div className="card"><p className="label">Overage</p><p className="font-display text-3xl">{usd(rep.overage.spent_cents)}</p><p className="mt-1 text-xs text-gray-500">{mins(rep.overage.seconds)} over plan{rep.overage.enabled && rep.overage.cap_cents ? `, ${usd(rep.overage.remaining_cents)} of the ${usd(rep.overage.cap_cents)} cap left` : ", overage off"}</p></div></div>
          </div>)}
      </Section>
    </div>
  );
}
