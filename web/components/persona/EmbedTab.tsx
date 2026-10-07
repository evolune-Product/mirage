"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CalendarCheck, Code2, ExternalLink, Inbox, Plus, Save, Send, Trash2 } from "lucide-react";
import { api, errText, fmtDur, Widget } from "@/lib/api";
import { Badge, ConfirmDialog, CopyButton, Field, Section, Spinner, Toggle, toast } from "@/components/ui";
import { Callout, Segmented } from "@/components/kit";

const FIELDS = ["name", "email", "phone", "company", "interest", "notes"] as const;
type Lc = { enabled: boolean; required_fields: string[]; require_consent: boolean; disclosure: string };
type Integ = { booking: { enabled: boolean; webhook_url: string; has_secret: boolean }; notify: { enabled: boolean; webhook_url: string; has_secret: boolean } };

function Widgets({ pid }: { pid: string }) {
  const [list, setList] = useState<Widget[] | null>(null); const [busy, setBusy] = useState(false); const [err, setErr] = useState(""); const [rm, setRm] = useState<Widget | null>(null);
  const [f, setF] = useState({ label: "Talk to us", color: "#6d5efc", position: "bottom-right", domains: "", greeting: "", language: "", max_seconds: "300", max_total: "3600" });
  const load = useCallback(() => api<Widget[]>(`/v1/widgets?persona_id=${pid}`).then(setList).catch((x) => { setList([]); if (!/404/.test(x.message)) toast.error(x); }), [pid]);
  useEffect(() => { setList(null); load(); }, [load]);
  async function create(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try { await api(`/v1/personas/${pid}/widget`, { body: { label: f.label, color: f.color, position: f.position, allowed_domains: f.domains.split(",").map((d) => d.trim()).filter(Boolean), greeting: f.greeting, language: f.language, max_seconds: Number(f.max_seconds), max_total_seconds: Number(f.max_total) } }); toast.success("Widget created. Copy the snippet below."); load(); }
    catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  return (
    <Section title="Website widget" icon={<Code2 size={16} className="text-vocalface-cyan" />} hint="A 'Talk to us' button for your site: paste one script tag, visitors talk to this persona without an account. Minutes are charged to you within the limits you set.">
      {list === null ? <div className="h-16 animate-pulse rounded-xl bg-white/5" /> : list.length === 0 ? <p className="mb-3 rounded-xl border border-dashed border-white/15 p-3.5 text-sm text-gray-500">No widget yet.</p> : (
        <ul className="mb-4 space-y-3" data-testid="widgets">{list.map((w) => (
          <li key={w.token} className={`rounded-xl border border-white/10 bg-white/[0.03] p-3.5 ${w.limits.revoked ? "opacity-60" : ""}`}>
            <div className="flex flex-wrap items-center gap-2"><span className="h-3 w-3 rounded-full" style={{ background: w.color }} /><p className="min-w-0 flex-1 truncate text-sm font-medium">{w.label}</p><Badge s={w.limits.revoked ? "revoked" : "active"} /></div>
            <p className="mt-1 text-xs text-gray-500">{w.position}; {w.allowed_domains.length ? `only on ${w.allowed_domains.join(", ")}` : "any website (add allowed domains to restrict)"}; {w.limits.sessions_started} sessions, {fmtDur(w.limits.used_seconds)} of {fmtDur(w.limits.max_total_seconds)} used</p>
            <p className="label mt-3">Paste before &lt;/body&gt; on your site</p>
            <div className="flex items-start gap-2 rounded-lg bg-black/40 p-2.5"><code data-testid="snippet" className="min-w-0 flex-1 break-all font-mono text-[11px] text-gray-200">{w.snippet}</code><CopyButton text={w.snippet} label="" /></div>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs"><a className="inline-flex items-center gap-1 text-vocalface-cyan hover:underline" href={w.share_url} target="_blank" rel="noreferrer"><ExternalLink size={12} />Open guest page</a>{!w.limits.revoked && <button className="inline-flex items-center gap-1 text-vocalface-rose hover:underline" onClick={() => setRm(w)}><Trash2 size={12} />Revoke</button>}</div>
          </li>))}</ul>)}
      <form onSubmit={create} className="space-y-3 rounded-xl bg-white/[0.03] p-4">
        <p className="text-sm font-medium">Create a widget</p>
        <div className="grid gap-3 sm:grid-cols-2"><Field label="Button label"><input className="input" required maxLength={40} value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} aria-label="Widget label" /></Field>
          <Field label="Colour"><div className="flex items-center gap-2"><input type="color" aria-label="Widget colour" value={f.color} onChange={(e) => setF({ ...f, color: e.target.value })} className="h-10 w-12 cursor-pointer rounded border border-white/10 bg-transparent" /><code className="text-xs text-gray-400">{f.color}</code></div></Field></div>
        <div><label className="label">Position</label><Segmented size="sm" label="Position" value={f.position} onChange={(v) => setF({ ...f, position: v })} options={[{ id: "bottom-right", label: "Bottom right" }, { id: "bottom-left", label: "Bottom left" }]} /></div>
        <Field label="Allowed domains" hint="Comma separated, e.g. example.com, shop.example.com. Empty = works on any site (anyone could copy your script and spend your minutes)."><input className="input" placeholder="example.com" value={f.domains} onChange={(e) => setF({ ...f, domains: e.target.value })} aria-label="Allowed domains" /></Field>
        <div className="grid gap-3 sm:grid-cols-2"><Field label="Max per visit (s)"><input className="input" type="number" min={10} max={3600} value={f.max_seconds} onChange={(e) => setF({ ...f, max_seconds: e.target.value })} /></Field><Field label="Total cap (s)"><input className="input" type="number" min={10} max={86400} value={f.max_total} onChange={(e) => setF({ ...f, max_total: e.target.value })} /></Field></div>
        {err && <Callout tone="bad">{err}</Callout>}
        <button className="btn" disabled={busy}>{busy ? <Spinner size={14} /> : <Plus size={14} />}Create widget</button>
      </form>
      <ConfirmDialog open={!!rm} title="Revoke this widget?" body="The button stops working on your site immediately and running chats end. This cannot be undone." confirmLabel="Revoke" onClose={() => setRm(null)}
        onConfirm={async () => { try { await api(`/v1/widgets/${rm!.token}`, { method: "DELETE" }); setRm(null); toast.success("Widget revoked."); load(); } catch (x) { toast.error(x); } }} />
    </Section>
  );
}

function LeadCapture({ pid }: { pid: string }) {
  const [c, setC] = useState<Lc | null>(null); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  useEffect(() => { setC(null); api<Lc>(`/v1/personas/${pid}/lead-capture`).then(setC).catch(() => setC({ enabled: false, required_fields: ["name"], require_consent: true, disclosure: "" })); }, [pid]);
  if (!c) return <div className="h-16 animate-pulse rounded-xl bg-white/5" />;
  async function save() { setBusy(true); setErr(""); try { setC(await api<Lc>(`/v1/personas/${pid}/lead-capture`, { method: "PUT", body: c })); toast.success("Lead capture saved."); } catch (x) { setErr(errText(x)); } finally { setBusy(false); } }
  return (
    <Section title="Lead capture" icon={<Inbox size={16} className="text-vocalface-mint" />} hint="Lets the agent politely ask for contact details and save them. It explains how the data is used first and never pushes."
      action={<Link href="/dashboard/leads" className="text-xs text-vocalface-cyan hover:underline">View leads</Link>}>
      <div className="space-y-3 rounded-xl border border-white/10 bg-white/[0.03] p-4">
        <Toggle checked={c.enabled} onChange={(v) => setC({ ...c, enabled: v })} label="Collect leads in conversations" />
        <div><p className="label">Required details</p><div className="flex flex-wrap gap-1.5">{FIELDS.map((k) => { const on = c.required_fields.includes(k); return <button key={k} type="button" aria-pressed={on} onClick={() => setC({ ...c, required_fields: on ? c.required_fields.filter((x) => x !== k) : [...c.required_fields, k] })} className={`rounded-full border px-2.5 py-1 text-xs ${on ? "border-vocalface-violet/60 bg-vocalface-violet/15 text-white" : "border-white/10 text-gray-400"}`}>{k}</button>; })}</div></div>
        <Toggle checked={c.require_consent} onChange={(v) => setC({ ...c, require_consent: v })} label="Require a spoken yes before saving" hint="Recommended. Turning it off can break privacy rules in many countries." />
        <Field label="What the agent says about data use" hint="Stored with each lead as proof of what the person agreed to."><textarea className="input h-20" placeholder="We use your details only to follow up about your enquiry." value={c.disclosure} onChange={(e) => setC({ ...c, disclosure: e.target.value })} aria-label="Disclosure" /></Field>
        {err && <Callout tone="bad">{err}</Callout>}
        <button type="button" className="btn" disabled={busy} onClick={save}>{busy ? <Spinner size={14} /> : <Save size={14} />}Save lead capture</button>
      </div>
    </Section>
  );
}

function Integrations({ pid }: { pid: string }) {
  const [i, setI] = useState<Integ | null>(null); const [f, setF] = useState({ bu: "", bs: "", nu: "", ns: "" }); const [busy, setBusy] = useState(""); const [res, setRes] = useState<Record<string, string>>({});
  const load = useCallback(() => api<Integ>(`/v1/personas/${pid}/integrations`).then((x) => { setI(x); setF((p) => ({ ...p, bu: x.booking.webhook_url, nu: x.notify.webhook_url, bs: "", ns: "" })); }).catch(() => setI({ booking: { enabled: false, webhook_url: "", has_secret: false }, notify: { enabled: false, webhook_url: "", has_secret: false } })), [pid]);
  useEffect(() => { setI(null); load(); }, [load]);
  if (!i) return <div className="h-16 animate-pulse rounded-xl bg-white/5" />;
  async function save() { setBusy("save"); try { const b: Record<string, string> = { booking_webhook_url: f.bu, notify_webhook_url: f.nu }; if (f.bs) b.booking_secret = f.bs; if (f.ns) b.notify_secret = f.ns; await api(`/v1/personas/${pid}/integrations`, { method: "PUT", body: b }); toast.success("Integrations saved."); load(); } catch (x) { toast.error(x); } finally { setBusy(""); } }
  async function test(which: "booking" | "notify") { setBusy(which); try { const r = await api<{ ok: boolean; response: unknown }>(`/v1/personas/${pid}/integrations/test`, { body: { which } }); setRes((p) => ({ ...p, [which]: r.ok ? "Your endpoint answered OK." : `Your endpoint did not accept the test: ${JSON.stringify(r.response).slice(0, 160)}` })); } catch (x) { setRes((p) => ({ ...p, [which]: errText(x) })); } finally { setBusy(""); } }
  const row = (which: "booking" | "notify", label: string, hint: string, u: string, s: string, setU: (v: string) => void, setS: (v: string) => void) => (
    <div className="space-y-2 rounded-xl border border-white/10 bg-white/[0.03] p-3.5"><p className="flex items-center gap-2 text-sm font-medium">{label}{i[which].enabled && <Badge s="active" />}</p><p className="text-xs text-gray-500">{hint}</p>
      <input className="input" type="url" placeholder="https://hooks.zapier.com/..." value={u} onChange={(e) => setU(e.target.value)} aria-label={`${label} webhook URL`} />
      <input className="input font-mono text-xs" type="password" autoComplete="new-password" placeholder={i[which].has_secret ? "Secret saved. Type to replace." : "Signing secret (optional, write-only)"} value={s} onChange={(e) => setS(e.target.value)} aria-label={`${label} secret`} />
      {i[which].enabled && <div className="flex flex-wrap items-center gap-2"><button type="button" className="btn-ghost !px-3 !py-1.5 text-xs" disabled={!!busy} onClick={() => test(which)}>{busy === which ? <Spinner size={13} /> : <Send size={13} />}Send test call</button>{res[which] && <span className="text-xs text-gray-400" data-testid={`test-${which}`}>{res[which]}</span>}</div>}</div>);
  return (
    <Section title="Booking and notifications" icon={<CalendarCheck size={16} className="text-vocalface-amber" />} hint="Optional webhooks (Cal.com, Zapier, Make, n8n). Setting a URL gives the agent a tool to use it. The agent never claims a meeting is confirmed.">
      <div className="space-y-3">{row("booking", "Book a meeting", "Called when a visitor wants a time. VocalFace sends name, email and preferred time.", f.bu, f.bs, (v) => setF({ ...f, bu: v }), (v) => setF({ ...f, bs: v }))}
        {row("notify", "Notify my team", "Called to alert you (for example an urgent request).", f.nu, f.ns, (v) => setF({ ...f, nu: v }), (v) => setF({ ...f, ns: v }))}
        <button type="button" className="btn" disabled={!!busy} onClick={save}>{busy === "save" ? <Spinner size={14} /> : <Save size={14} />}Save integrations</button></div>
    </Section>
  );
}

export default function EmbedTab({ pid }: { pid: string }) {
  return <div><Widgets pid={pid} /><LeadCapture pid={pid} /><Integrations pid={pid} /></div>;
}
