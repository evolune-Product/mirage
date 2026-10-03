"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronLeft, ChevronRight, Download, Inbox, Search, Trash2 } from "lucide-react";
import { API_URL, api, errText, getKey, getWorkspace, Lead, Persona, fmtDate } from "@/lib/api";
import { ConfirmDialog, Empty, Modal, Shell, Skeleton, Spinner, Stat, toast } from "@/components/ui";

const PAGE = 25;
type Page = { items: Lead[]; total: number; limit: number; offset: number };

export default function Leads() {
  const [ps, setPs] = useState<Persona[]>([]); const [pid, setPid] = useState(""); const [since, setSince] = useState(""); const [until, setUntil] = useState(""); const [q, setQ] = useState(""); const [dq, setDq] = useState("");
  const [consent, setConsent] = useState(""); const [off, setOff] = useState(0); const [data, setData] = useState<Page | null>(null); const [err, setErr] = useState(""); const [open, setOpen] = useState<Lead | null>(null);
  const [del, setDel] = useState<Lead | null>(null); const [exp, setExp] = useState(false);
  useEffect(() => { api<Persona[]>("/v1/personas").then(setPs).catch(() => {}); }, []);
  useEffect(() => { const t = setTimeout(() => setDq(q), 300); return () => clearTimeout(t); }, [q]);
  const qs = useMemo(() => { const p = new URLSearchParams(); if (pid) p.set("persona_id", pid); if (since) p.set("since", since); if (until) p.set("until", until); if (dq.trim()) p.set("q", dq.trim()); if (consent) p.set("consent", consent); return p; }, [pid, since, until, dq, consent]);
  useEffect(() => { setOff(0); }, [qs]);
  const load = useCallback(() => { const p = new URLSearchParams(qs); p.set("limit", String(PAGE)); p.set("offset", String(off)); setErr(""); api<Page>(`/v1/leads?${p}`).then(setData).catch((x) => { setErr(errText(x)); setData({ items: [], total: 0, limit: PAGE, offset: 0 }); }); }, [qs, off]);
  useEffect(() => { setData(null); load(); }, [load]);
  const pname = (id: string) => ps.find((p) => p.id === id)?.name ?? id;
  async function exportCsv() {
    setExp(true);
    try {
      const h: Record<string, string> = { "x-api-key": getKey() }; const w = getWorkspace(); if (w) h["x-workspace"] = w;
      const r = await fetch(`${API_URL}/v1/leads/export.csv?${qs}`, { headers: h }); if (!r.ok) throw new Error(`${r.status}: ${r.statusText}`);
      const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = `mirage-leads-${new Date().toISOString().slice(0, 10)}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); toast.success("CSV downloaded.");
    } catch (x) { toast.error(x); } finally { setExp(false); }
  }
  const filtered = !!(pid || since || until || dq || consent);
  const total = data?.total ?? 0; const pages = Math.max(1, Math.ceil(total / PAGE)); const page = Math.floor(off / PAGE) + 1;
  return (
    <Shell title="Leads" subtitle="People your agents spoke to who agreed to share contact details. Each was asked for consent first."
      action={<button className="btn" disabled={exp || total === 0} onClick={exportCsv}>{exp ? <Spinner size={14} /> : <Download size={15} />}Export CSV</button>}>
      <div className="mb-5 grid gap-3 md:grid-cols-4">
        <Stat label={filtered ? "Matching leads" : "Total leads"} value={data ? total : "-"} />
        <div className="card !p-4 md:col-span-3"><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="lg:col-span-2"><label className="label">Search</label><div className="relative"><Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" /><input className="input !pl-9" placeholder="name, email, company..." value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search leads" /></div></div>
          <div><label className="label">Persona</label><select className="input" value={pid} onChange={(e) => setPid(e.target.value)} aria-label="Persona filter"><option value="">All</option>{ps.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
          <div><label className="label">From</label><input type="date" className="input" value={since} onChange={(e) => setSince(e.target.value)} aria-label="From date" /></div>
          <div><label className="label">To</label><input type="date" className="input" value={until} onChange={(e) => setUntil(e.target.value)} aria-label="To date" /></div></div>
          <div className="mt-3 flex items-center gap-3 text-xs text-gray-400"><label className="flex items-center gap-1.5">Consent<select className="input !w-auto !py-1 text-xs" value={consent} onChange={(e) => setConsent(e.target.value)} aria-label="Consent filter"><option value="">any</option><option value="true">given</option><option value="false">not given</option></select></label>
            {filtered && <button className="text-mirage-cyan hover:underline" onClick={() => { setPid(""); setSince(""); setUntil(""); setQ(""); setConsent(""); }}>Clear filters</button>}</div></div>
      </div>
      {err && <p className="mb-3 text-sm text-mirage-rose" role="alert">{err}</p>}
      {data === null ? <Skeleton className="h-64" /> : data.items.length === 0 ? (
        filtered ? <Empty kind="knowledge" title="No leads match" hint="Try a wider date range or clear the filters." /> : <Empty kind="conversation" title="No leads yet" hint="Create a persona from a template with lead capture on, share its widget or guest link, and contacts will land here." action={<Link href="/dashboard/personas" className="btn-grad">Go to personas</Link>} />
      ) : (<>
        <div className="overflow-x-auto rounded-2xl border border-white/10"><table className="w-full min-w-[720px] text-sm" data-testid="leads-table">
          <thead className="bg-white/[0.03] text-left text-xs uppercase tracking-wider text-gray-500"><tr><th className="px-4 py-3 font-medium">When</th><th className="px-4 py-3 font-medium">Name</th><th className="px-4 py-3 font-medium">Contact</th><th className="px-4 py-3 font-medium">Company</th><th className="px-4 py-3 font-medium">Persona</th><th className="px-4 py-3 font-medium">Consent</th></tr></thead>
          <tbody>{data.items.map((l) => (
            <tr key={l.id} className="cursor-pointer border-t border-white/5 hover:bg-white/[0.04]" onClick={() => setOpen(l)} tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter") setOpen(l); }}>
              <td className="whitespace-nowrap px-4 py-2.5 text-gray-400">{fmtDate(l.created_at)}</td><td className="px-4 py-2.5">{l.name || "-"}</td>
              <td className="px-4 py-2.5 text-gray-300"><span className="block max-w-[14rem] truncate">{l.email || "-"}</span><span className="text-xs text-gray-500">{l.phone}</span></td>
              <td className="px-4 py-2.5 text-gray-400">{l.company || "-"}</td><td className="px-4 py-2.5 text-gray-400">{pname(l.persona_id)}</td><td className="px-4 py-2.5"><span className={`rounded-full px-2.5 py-0.5 text-xs ring-1 ring-inset ${l.consent ? "bg-mirage-mint/10 text-mirage-mint ring-mirage-mint/25" : "bg-mirage-amber/10 text-mirage-amber ring-mirage-amber/25"}`}>{l.consent ? "given" : "not given"}</span></td></tr>))}</tbody></table></div>
        <div className="mt-3 flex items-center justify-between text-xs text-gray-500"><span>{off + 1}-{Math.min(off + PAGE, total)} of {total}</span>
          <span className="flex items-center gap-2"><button className="btn-ghost !px-2.5 !py-1.5" aria-label="Previous page" disabled={off === 0} onClick={() => setOff(off - PAGE)}><ChevronLeft size={14} /></button>page {page} / {pages}<button className="btn-ghost !px-2.5 !py-1.5" aria-label="Next page" disabled={off + PAGE >= total} onClick={() => setOff(off + PAGE)}><ChevronRight size={14} /></button></span></div>
      </>)}
      <Modal side open={!!open} onClose={() => setOpen(null)} title={open?.name || "Lead"}>
        {open && <div className="space-y-4 text-sm">
          <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-2.5">{([["Email", open.email], ["Phone", open.phone], ["Company", open.company], ["Interested in", open.interest], ["Notes", open.notes], ["Persona", pname(open.persona_id)], ["Captured", fmtDate(open.created_at)], ["Source", open.source]] as [string, string][]).map(([k, v]) => <div key={k} className="contents"><dt className="text-gray-500">{k}</dt><dd className="break-words text-gray-200">{v || "-"}</dd></div>)}</dl>
          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3.5"><p className="label">Consent</p><p className="text-gray-300">{open.consent ? "Given." : "Not given."}</p>{open.consent_text && <p className="mt-1 text-xs italic text-gray-500">&ldquo;{open.consent_text}&rdquo;</p>}</div>
          {open.conversation_id && <Link href="/dashboard/conversations" className="inline-flex items-center gap-1.5 text-xs text-mirage-cyan hover:underline"><Inbox size={12} />From conversation {open.conversation_id}</Link>}
          <button className="btn-ghost !border-mirage-rose/40 !text-mirage-rose hover:!bg-mirage-rose/10" onClick={() => setDel(open)}><Trash2 size={14} />Delete permanently</button>
        </div>}
      </Modal>
      <ConfirmDialog open={!!del} title="Delete this lead?" body="This permanently erases the contact details (for example when the person asks you to). It cannot be undone." confirmLabel="Delete lead" onClose={() => setDel(null)}
        onConfirm={async () => { try { await api(`/v1/leads/${del!.id}`, { method: "DELETE" }); setDel(null); setOpen(null); toast.success("Lead deleted."); load(); } catch (x) { toast.error(x); } }} />
    </Shell>
  );
}
