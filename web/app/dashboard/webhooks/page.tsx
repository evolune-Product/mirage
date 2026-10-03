"use client";
import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Plus, RefreshCw, Send, Trash2, Webhook as WebhookIcon, RotateCw, X, Check } from "lucide-react";
import { api, Webhook, Delivery, fmtDate } from "@/lib/api";
import { Shell, Empty, Modal, SkeletonCards, Badge, CopyButton, ConfirmDialog, SecretOnce, Spinner, Field, Toggle, toast } from "@/components/ui";

function EventPicker({ all, value, onChange }: { all: string[]; value: string[]; onChange: (v: string[]) => void }) {
  const every = value.includes("*");
  const toggle = (e: string) => onChange(value.includes(e) ? value.filter((x) => x !== e) : [...value, e]);
  return (
    <div>
      <Toggle checked={every} onChange={(v) => onChange(v ? ["*"] : [])} label="All events" hint="Receive everything, including events added in future." />
      <div className={`mt-3 flex flex-wrap gap-1.5 ${every ? "pointer-events-none opacity-40" : ""}`} role="group" aria-label="Events">
        {all.map((e) => { const on = every || value.includes(e);
          return <button type="button" key={e} aria-pressed={on} onClick={() => toggle(e)} className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 font-mono text-[11px] transition ${on ? "border-mirage-violet/50 bg-mirage-violet/15 text-white" : "border-white/10 text-gray-400 hover:text-white"}`}>{on && <Check size={11} />}{e}</button>; })}
      </div>
    </div>
  );
}

function Deliveries({ hook }: { hook: Webhook | null }) {
  const [rows, setRows] = useState<Delivery[] | null>(null); const [busy, setBusy] = useState("");
  const load = useCallback(() => { if (hook) api<Delivery[]>(`/v1/webhooks/${hook.id}/deliveries`).then(setRows).catch((x) => { toast.error(x); setRows([]); }); }, [hook]);
  useEffect(() => { setRows(null); load(); if (!hook) return; const t = setInterval(load, 5000); return () => clearInterval(t); }, [load, hook]);
  async function retry(id: string) { setBusy(id); try { await api(`/v1/webhooks/deliveries/${id}/retry`, { method: "POST", body: {} }); load(); } catch (x) { toast.error(x); } finally { setBusy(""); } }
  async function test() { if (!hook) return; setBusy("test"); try { await api(`/v1/webhooks/${hook.id}/test`, { method: "POST", body: {} }); toast.info("Test event sent."); load(); } catch (x) { toast.error(x); } finally { setBusy(""); } }
  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-2">
        <p className="text-sm font-medium">Delivery log</p>
        <div className="flex gap-2"><button className="btn-ghost !px-3 !py-1.5 text-xs" onClick={load} aria-label="Refresh log"><RefreshCw size={13} /></button>
          <button className="btn !px-3.5 !py-1.5 text-xs" disabled={busy === "test"} onClick={test}>{busy === "test" ? <Spinner size={13} /> : <Send size={13} />}Send test event</button></div>
      </div>
      {rows === null ? <div className="h-20 animate-pulse rounded-xl bg-white/5" /> : rows.length === 0 ? (
        <p className="rounded-xl border border-dashed border-white/15 p-4 text-sm text-gray-500">No deliveries yet. Send a test event, or trigger a real one such as ending a conversation.</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-white/10">
          <table className="w-full min-w-[34rem] text-left text-xs" data-testid="deliveries">
            <thead className="bg-white/[0.03] text-[10px] uppercase tracking-wider text-gray-500"><tr><th className="px-3 py-2">Event</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Tries</th><th className="px-3 py-2">Code</th><th className="px-3 py-2">When</th><th /></tr></thead>
            <tbody>{rows.map((d) => (
              <tr key={d.id} className="border-t border-white/5 align-top">
                <td className="px-3 py-2 font-mono text-gray-200">{d.event}</td>
                <td className="px-3 py-2"><Badge s={d.status} />{d.last_error && d.status !== "delivered" && <p className="mt-1 line-clamp-2 max-w-[16rem] break-words text-[11px] text-gray-500" title={d.last_error ?? ""}>{d.last_error}</p>}
                  {d.status === "pending" && d.next_attempt_at && <p className="mt-1 text-[11px] text-gray-500">retry {fmtDate(d.next_attempt_at)}</p>}</td>
                <td className="px-3 py-2 font-mono">{d.attempts}/6</td>
                <td className="px-3 py-2 font-mono">{d.last_status_code ?? "-"}</td>
                <td className="whitespace-nowrap px-3 py-2 text-gray-400">{fmtDate(d.delivered_at || d.created_at)}</td>
                <td className="px-3 py-2 text-right">{d.status !== "delivered" && <button className="inline-flex items-center gap-1 text-mirage-cyan hover:underline" disabled={busy === d.id} onClick={() => retry(d.id)}><RotateCw size={11} />Retry</button>}</td>
              </tr>))}</tbody>
          </table>
        </div>)}
    </div>
  );
}

export default function Webhooks() {
  const [list, setList] = useState<Webhook[] | null>(null); const [events, setEvents] = useState<string[]>([]);
  const [open, setOpen] = useState(false); const [busy, setBusy] = useState(false); const [sel, setSel] = useState<Webhook | null>(null);
  const [f, setF] = useState({ url: "", description: "", events: ["*"] as string[] }); const [secret, setSecret] = useState<{ id: string; secret: string } | null>(null);
  const [del, setDel] = useState<Webhook | null>(null); const [rot, setRot] = useState<Webhook | null>(null);
  const load = useCallback(() => api<Webhook[]>("/v1/webhooks").then(setList).catch((x) => { toast.error(x); setList((l) => l ?? []); }), []);
  useEffect(() => { load(); api<{ events: string[] }>("/v1/webhooks/events").then((r) => setEvents(r.events)).catch(() => {}); }, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault(); if (!f.events.length) { toast.error("Pick at least one event."); return; } setBusy(true);
    try { const w = await api<Webhook>("/v1/webhooks", { body: f }); setSecret({ id: w.id, secret: w.secret! }); setOpen(false); setF({ url: "", description: "", events: ["*"] }); load(); }
    catch (x) { toast.error(x); } finally { setBusy(false); }
  }
  async function patch(w: Webhook, body: Partial<Webhook>) { try { await api(`/v1/webhooks/${w.id}`, { method: "PATCH", body }); load(); setSel((s) => (s && s.id === w.id ? { ...s, ...body } : s)); } catch (x) { toast.error(x); } }
  const newBtn = <button className="btn-grad" onClick={() => setOpen(true)}><Plus size={16} />Add endpoint</button>;

  return (
    <Shell title="Webhooks" subtitle="Get an HTTPS call when a conversation ends, an objective completes, a video is ready and more. Signed, retried, logged." action={newBtn}>
      {secret && <div className="mb-6"><SecretOnce title="Copy your signing secret now" note="It verifies the Mirage-Signature header on every delivery. For security it is only shown this once; rotate it if you lose it." secret={secret.secret} onDone={() => setSecret(null)} /></div>}
      {list === null ? <SkeletonCards n={2} h="h-32" /> : list.length === 0 ? (
        <Empty kind="webhook" title="No webhook endpoints" hint="Add an endpoint and choose which events to receive. Each delivery is signed with HMAC-SHA256 and retried with backoff." action={newBtn} />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {list.map((w, i) => (
            <motion.div key={w.id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }} className="card flex flex-col gap-3">
              <div className="flex items-start gap-3">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-white/5 text-mirage-cyan"><WebhookIcon size={18} /></span>
                <div className="min-w-0 flex-1"><p className="break-all text-sm font-medium">{w.url}</p><p className="truncate text-xs text-gray-500">{w.description || w.id}</p></div>
                <Badge s={w.active ? "active" : "revoked"} />
              </div>
              <div className="flex flex-wrap gap-1.5">{(w.events.includes("*") ? ["all events"] : w.events).slice(0, 4).map((e) => <span key={e} className="rounded-full bg-white/5 px-2 py-0.5 font-mono text-[11px] text-gray-300">{e}</span>)}
                {!w.events.includes("*") && w.events.length > 4 && <span className="text-[11px] text-gray-500">+{w.events.length - 4}</span>}</div>
              <div className="mt-auto flex flex-wrap gap-2">
                <button className="btn !px-3.5 !py-1.5 text-xs" onClick={() => setSel(w)}>Deliveries</button>
                <button className="btn-ghost !px-3 !py-1.5 text-xs" onClick={() => patch(w, { active: !w.active })}>{w.active ? "Pause" : "Resume"}</button>
                <button className="btn-ghost !px-3 !py-1.5 text-xs" onClick={() => setRot(w)}><RotateCw size={12} />Rotate secret</button>
                <button aria-label={`Delete ${w.url}`} className="btn-ghost !px-2.5 !py-1.5 text-xs hover:!text-mirage-rose" onClick={() => setDel(w)}><Trash2 size={13} /></button>
              </div>
            </motion.div>))}
        </div>)}

      <Modal open={open} onClose={() => setOpen(false)} title="Add endpoint">
        <form onSubmit={create} className="space-y-4">
          <Field label="Endpoint URL"><input className="input" type="url" required autoFocus placeholder="https://example.com/mirage/hook" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>
          <Field label="Description (optional)"><input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
          <div><label className="label">Events</label><EventPicker all={events} value={f.events} onChange={(v) => setF({ ...f, events: v })} /></div>
          <button className="btn-grad w-full" disabled={busy}>{busy && <Spinner />}Create endpoint</button>
        </form>
      </Modal>

      <Modal side wide open={!!sel} onClose={() => setSel(null)} title="Webhook">
        {sel && (<div className="space-y-5">
          <div><p className="break-all font-medium">{sel.url}</p><p className="font-mono text-xs text-gray-500">{sel.id}</p></div>
          <div><label className="label">Events</label><EventPicker all={events} value={sel.events} onChange={(v) => { setSel({ ...sel, events: v }); }} />
            <button className="btn mt-3 !px-4 !py-1.5 text-xs" disabled={!sel.events.length} onClick={() => patch(sel, { events: sel.events }).then(() => toast.success("Events updated."))}>Save events</button></div>
          <Deliveries hook={sel} />
        </div>)}
      </Modal>

      <ConfirmDialog open={!!del} title="Delete endpoint?" body={<>Stop sending events to <b className="break-all">{del?.url}</b>? Its delivery log is removed too.</>} confirmLabel="Delete endpoint" onClose={() => setDel(null)}
        onConfirm={async () => { try { await api(`/v1/webhooks/${del!.id}`, { method: "DELETE" }); setDel(null); load(); toast.success("Endpoint deleted."); } catch (x) { toast.error(x); } }} />
      <ConfirmDialog open={!!rot} title="Rotate signing secret?" danger={false} body="A new secret is generated and shown once. Your receiver must switch to it, deliveries signed with the old one will fail verification." confirmLabel="Rotate secret" onClose={() => setRot(null)}
        onConfirm={async () => { try { const w = await api<Webhook>(`/v1/webhooks/${rot!.id}/rotate-secret`, { method: "POST", body: {} }); setSecret({ id: w.id, secret: w.secret! }); setRot(null); window.scrollTo({ top: 0, behavior: "smooth" }); } catch (x) { toast.error(x); } }} />
    </Shell>
  );
}
