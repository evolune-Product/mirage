"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { Clapperboard, Cpu, ExternalLink, Languages, Layers, Sparkles, Wand2, ChevronDown } from "lucide-react";
import { api, Replica, Video, Lang, fileUrl, saveId, fmtDate } from "@/lib/api";
import { Shell, Badge, Empty, Skeleton, CopyButton, Tabs, Field, Spinner, refreshCredits, toast } from "@/components/ui";

const MAX = 1000;
type Mode = "single" | "bulk" | "translate";
type BatchItem = { video_id: string; row_index: number; language: string; status: string; output_url: string | null; variables: Record<string, string>; script: string };
type Batch = { id: string; kind: string; replica_id: string; total: number; created_at: string; counts: Record<string, number>; completed: boolean; items?: BatchItem[] };
const varsOf = (t: string) => [...new Set([...t.matchAll(/\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g)].map((m) => m[1]))];

/** Parse pasted CSV (header row) or a JSON array of objects. */
function parseRows(txt: string): { rows: Record<string, string>[]; error?: string } {
  const t = txt.trim(); if (!t) return { rows: [] };
  if (t.startsWith("[") || t.startsWith("{")) {
    try { const j = JSON.parse(t); const arr = Array.isArray(j) ? j : [j];
      if (arr.some((r) => !r || typeof r !== "object" || Array.isArray(r))) return { rows: [], error: "Each JSON row must be an object." };
      return { rows: arr.map((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, String(v)]))) };
    } catch (e) { return { rows: [], error: "Invalid JSON: " + (e as Error).message }; }
  }
  const cells = (line: string) => { const out: string[] = []; let cur = "", q = false;
    for (let i = 0; i < line.length; i++) { const c = line[i];
      if (q) { if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; }
      else if (c === '"') q = true; else if (c === ",") { out.push(cur.trim()); cur = ""; } else cur += c; }
    out.push(cur.trim()); return out; };
  const lines = t.split(/\r?\n/).filter((l) => l.trim());
  const head = cells(lines[0]);
  if (head.some((h) => !h)) return { rows: [], error: "The first CSV line must be a header of column names." };
  return { rows: lines.slice(1).map((l) => { const c = cells(l); return Object.fromEntries(head.map((h, i) => [h, c[i] ?? ""])); }) };
}

export default function Videos() {
  const [reps, setReps] = useState<Replica[] | null>(null); const [rid, setRid] = useState(""); const [vids, setVids] = useState<Video[] | null>(null); const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<Mode>("single"); const [script, setScript] = useState(""); const [sample, setSample] = useState<Record<string, string>>({});
  const [csv, setCsv] = useState(""); const [langs, setLangs] = useState<Lang[]>([]); const [pick, setPick] = useState<string[]>([]); const [orig, setOrig] = useState(true);
  const [batches, setBatches] = useState<Batch[]>([]); const [open, setOpen] = useState<string | null>(null); const [info, setInfo] = useState<Record<string, BatchItem>>({});
  const [preview, setPreview] = useState<string | null>(null);

  const load = useCallback(async () => { try { setVids([...(await api<Video[]>("/v1/videos"))].reverse()); } catch { setVids((v) => v ?? []); } }, []);
  const loadBatches = useCallback(async () => {
    try {
      const l = [...(await api<Batch[]>("/v1/video-batches"))].reverse(); setBatches(l);
      const det = await Promise.all(l.slice(0, 8).map((b) => api<Batch>(`/v1/video-batches/${b.id}`).catch(() => null)));
      const m: Record<string, BatchItem> = {}; det.forEach((b) => b?.items?.forEach((it) => { m[it.video_id] = it; })); setInfo(m);
      setBatches((cur) => cur.map((b) => det.find((x) => x?.id === b.id) ?? b));
    } catch { /* older servers: no batches */ }
  }, []);
  useEffect(() => {
    api<Replica[]>("/v1/replicas").then((l) => { const ok = l.filter((r) => r.status === "ready"); setReps(ok); if (ok[0]) setRid(ok[0].id); }).catch((x) => { toast.error(x); setReps([]); });
    api<Lang[]>("/v1/languages").then((l) => setLangs(l.filter((x) => x.tts))).catch(() => {});
    load(); loadBatches(); const t = setInterval(() => { load(); loadBatches(); }, 5000); return () => clearInterval(t);
  }, [load, loadBatches]);

  const vars = useMemo(() => varsOf(script), [script]);
  const parsed = useMemo(() => parseRows(csv), [csv]);
  const bulkVars = vars;
  const missingRows = useMemo(() => parsed.rows.map((r, i) => ({ i, miss: bulkVars.filter((v) => !(v in r) || r[v] === "") })).filter((x) => x.miss.length), [parsed, bulkVars]);
  const sampleOk = vars.every((v) => (sample[v] ?? "").trim());

  useEffect(() => { // live preview of {{variables}} for the single mode
    if (mode !== "single" || !vars.length || !sampleOk) { setPreview(null); return; }
    const t = setTimeout(() => api<{ rendered: string | null }>("/v1/videos/template/preview", { body: { script_template: script, variables: sample } }).then((r) => setPreview(r.rendered)).catch(() => setPreview(null)), 300);
    return () => clearTimeout(t);
  }, [mode, script, sample, vars.length, sampleOk]);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try {
      if (mode === "single") {
        const text = vars.length ? preview : script;
        if (!text) throw new Error("Fill in every variable first.");
        const v = await api<Video>("/v1/videos", { body: { replica_id: rid, script: text } }); saveId("videos", v.id); toast.success("Video queued.");
      } else if (mode === "bulk") {
        if (parsed.error) throw new Error(parsed.error); if (!parsed.rows.length) throw new Error("Paste at least one row."); if (parsed.rows.length > 200) throw new Error("Bulk is limited to 200 rows per batch.");
        const b = await api<Batch>("/v1/video-jobs/bulk", { body: { replica_id: rid, script_template: script, rows: parsed.rows } }); toast.success(`Batch queued: ${b.total} videos.`); setCsv(""); setOpen(b.id);
      } else {
        if (!pick.length) throw new Error("Pick at least one language.");
        const b = await api<Batch>("/v1/video-jobs/translate", { body: { replica_id: rid, script, languages: pick, include_original: orig } }); toast.success(`Queued ${b.total} variants.`); setOpen(b.id);
      }
      if (mode !== "bulk") setScript(""); setSample({}); load(); loadBatches(); refreshCredits();
    } catch (x) { toast.error(x); } finally { setBusy(false); }
  }
  const rname = (id: string) => reps?.find((r) => r.id === id)?.name ?? id;
  const can = !!rid && !!script.trim() && !busy && (mode === "single" ? (vars.length ? !!preview : true) : mode === "bulk" ? parsed.rows.length > 0 && !parsed.error && !missingRows.length && parsed.rows.length <= 200 : pick.length > 0);
  const label = mode === "single" ? "Generate video" : mode === "bulk" ? `Generate ${parsed.rows.length || ""} videos`.replace("  ", " ") : `Translate into ${pick.length || ""} languages`.replace("  ", " ");

  return (
    <Shell title="Videos" subtitle="Write a script, pick a replica, get a talking-head video. Personalise it with variables, run it in bulk, or translate it.">
      {reps !== null && reps.length === 0 ? (
        <Empty kind="video" title="No ready replica yet" hint="Videos need a replica that has consent and finished training." action={<Link href="/dashboard/replicas" className="btn-grad">Go to replicas</Link>} />
      ) : (
        <form onSubmit={submit} className="card">
          <Tabs value={mode} onChange={setMode} tabs={[{ id: "single", label: "Single video" }, { id: "bulk", label: "Bulk from rows" }, { id: "translate", label: "Translate" }]} />
          <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
            <div className="space-y-4">
              <Field label="Replica (ready only)"><select className="input" value={rid} onChange={(e) => setRid(e.target.value)}>{(reps ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Field>
              {mode === "translate" && (
                <div><label className="label">Languages</label>
                  <div className="flex flex-wrap gap-1.5" role="group" aria-label="Target languages">{langs.map((l) => { const on = pick.includes(l.code);
                    return <button type="button" key={l.code} aria-pressed={on} onClick={() => setPick(on ? pick.filter((x) => x !== l.code) : [...pick, l.code])} className={`rounded-full border px-2.5 py-1 text-xs transition ${on ? "border-mirage-violet/50 bg-mirage-violet/15 text-white" : "border-white/10 text-gray-400 hover:text-white"}`}>{l.name}</button>; })}</div>
                  <label className="mt-3 flex cursor-pointer items-center gap-2 text-xs text-gray-400"><input type="checkbox" checked={orig} onChange={(e) => setOrig(e.target.checked)} />Also render the original</label>
                  <p className="mt-2 text-xs text-gray-500">Each variant is translated by an LLM and re-rendered with that language&apos;s voice. It is not a time-aligned dub.</p>
                </div>)}
            </div>
            <div>
              <div className="mb-1.5 flex items-center justify-between gap-2"><label className="label !mb-0">{mode === "bulk" ? "Script template" : "Script"}</label><span className="font-mono text-xs text-gray-500">{script.length} chars - about {Math.max(0, Math.round(script.length / 15))}s of speech</span></div>
              <textarea className="input h-32" required aria-label="Script" placeholder={mode === "bulk" ? "Hi {{first_name}}, thanks for signing up to {{company}}..." : "Hi, I'm... Today I want to show you... (use {{first_name}} for personalisation)"} value={script} onChange={(e) => setScript(e.target.value)} />
              <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/10"><div className="h-full bg-mirage-gradient transition-all" style={{ width: Math.min(100, (script.length / MAX) * 100) + "%" }} /></div>
              {vars.length > 0 && <p className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-gray-400"><Wand2 size={12} className="text-mirage-amber" />Variables:{vars.map((v) => <code key={v} className="rounded bg-mirage-amber/10 px-1.5 py-0.5 font-mono text-mirage-amber">{`{{${v}}}`}</code>)}</p>}
            </div>
          </div>

          {mode === "single" && vars.length > 0 && (
            <div className="mt-4 rounded-xl bg-white/[0.03] p-4">
              <p className="mb-3 text-sm font-medium">Fill the variables</p>
              <div className="grid gap-3 sm:grid-cols-2">{vars.map((v) => <Field key={v} label={v}><input className="input" aria-label={`Variable ${v}`} value={sample[v] ?? ""} onChange={(e) => setSample({ ...sample, [v]: e.target.value })} /></Field>)}</div>
              <p className="label mt-4">Preview</p>
              <p className="rounded-xl border border-white/10 bg-black/30 p-3 text-sm text-gray-200" data-testid="preview">{preview ?? <span className="text-gray-500">Fill in every variable to preview the final script.</span>}</p>
            </div>)}

          {mode === "bulk" && (
            <div className="mt-4">
              <div className="mb-1.5 flex items-center justify-between"><label className="label !mb-0">Rows (CSV with a header line, or a JSON array)</label>
                <button type="button" className="text-xs text-mirage-cyan hover:underline" onClick={() => setCsv(JSON.stringify([{ first_name: "Ravi", company: "Acme" }, { first_name: "Maya", company: "Globex" }], null, 1))}>Insert example</button></div>
              <textarea className="input h-32 font-mono text-xs" aria-label="Rows" spellCheck={false} placeholder={"first_name,company\nRavi,Acme\nMaya,Globex"} value={csv} onChange={(e) => setCsv(e.target.value)} />
              <div className="mt-2 text-xs" data-testid="bulk-summary">
                {parsed.error ? <p className="text-mirage-rose">{parsed.error}</p> : parsed.rows.length === 0 ? <p className="text-gray-500">No rows yet.</p> : (<>
                  <p className={parsed.rows.length > 200 ? "text-mirage-rose" : "text-mirage-mint"}>{parsed.rows.length} {parsed.rows.length === 1 ? "video" : "videos"} will be generated{parsed.rows.length > 200 ? " (limit is 200 per batch)" : ""}.</p>
                  {missingRows.length > 0 && <p className="mt-1 text-mirage-rose">{missingRows.length} rows are missing a value: {missingRows.slice(0, 3).map((m) => `row ${m.i + 1} (${m.miss.join(", ")})`).join("; ")}{missingRows.length > 3 ? "..." : ""}</p>}
                  {script && !bulkVars.length && <p className="mt-1 text-mirage-amber">The template has no {"{{variables}}"}, so every video will be identical.</p>}
                  {!missingRows.length && bulkVars.length > 0 && <p className="mt-1 truncate text-gray-400">First row: {script.replace(/\{\{\s*([A-Za-z_]\w*)\s*\}\}/g, (_, v) => parsed.rows[0]?.[v] ?? "")}</p>}
                </>)}
              </div>
            </div>)}
          <button className="btn-grad mt-5" disabled={!can}>{busy ? <Spinner /> : mode === "translate" ? <Languages size={15} /> : mode === "bulk" ? <Layers size={15} /> : <Sparkles size={15} />}{label}</button>
        </form>
      )}

      {batches.length > 0 && (<>
        <h2 className="mb-3 mt-10 text-sm font-medium text-gray-300">Batches</h2>
        <div className="overflow-hidden rounded-2xl border border-white/10" data-testid="batches">
          {batches.map((b) => { const done = (b.counts.ready ?? 0); const err = b.counts.error ?? 0; const isOpen = open === b.id;
            return (
              <div key={b.id} className="border-b border-white/5 bg-ink-2/60 last:border-0">
                <button className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-left hover:bg-white/[0.04]" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : b.id)}>
                  <span className="grid h-8 w-8 place-items-center rounded-lg bg-white/5 text-gray-400">{b.kind === "translate" ? <Languages size={15} /> : <Layers size={15} />}</span>
                  <div className="min-w-0 flex-1 basis-40"><p className="text-sm capitalize">{b.kind} batch - {b.total} videos</p><p className="truncate font-mono text-[11px] text-gray-500">{b.id} - {fmtDate(b.created_at)}</p></div>
                  <div className="w-32"><div className="h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full bg-mirage-gradient" style={{ width: (done / Math.max(1, b.total)) * 100 + "%" }} /></div><p className="mt-1 text-[11px] text-gray-500">{done}/{b.total} ready{err ? `, ${err} failed` : ""}</p></div>
                  <Badge s={b.completed ? (err ? "error" : "completed") : (b.counts.queued ?? 0) === b.total ? "queued" : "rendering"} /><ChevronDown size={15} className={`text-gray-500 transition ${isOpen ? "rotate-180" : ""}`} />
                </button>
                {isOpen && <ul className="space-y-1.5 border-t border-white/5 px-4 py-3">{(b.items ?? []).map((it) => (
                  <li key={it.video_id} className="flex items-center gap-3 text-xs"><span className="w-14 shrink-0 font-mono text-gray-500">{b.kind === "translate" ? (it.language || "orig") : `#${it.row_index + 1}`}</span><span className="min-w-0 flex-1 truncate text-gray-300">{it.script}</span><Badge s={it.status} />
                    {it.output_url && <a className="text-mirage-cyan hover:underline" href={fileUrl(it.output_url)} target="_blank" rel="noreferrer">open</a>}</li>))}
                  {!b.items && <li className="text-xs text-gray-500">Loading...</li>}</ul>}
              </div>);
          })}
        </div>
      </>)}

      <h2 className="mb-3 mt-10 text-sm font-medium text-gray-300">Your videos</h2>
      {vids === null ? <div className="grid gap-4 md:grid-cols-2"><Skeleton className="h-64" /><Skeleton className="h-64" /></div> : vids.length === 0 ? (
        <Empty kind="video" title="No videos yet" hint="Generated videos show up here with an inline player as soon as they finish rendering." />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {vids.map((v, i) => { const it = info[v.id];
            return (
              <motion.div key={v.id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i, 8) * 0.04 }} className="card flex flex-col gap-3 !p-4">
                {v.output_url ? <video className="aspect-video w-full rounded-xl bg-black" controls preload="metadata" src={fileUrl(v.output_url)} />
                  : <div className="grid aspect-video w-full place-items-center rounded-xl border border-white/10 bg-[radial-gradient(circle_at_50%_40%,rgba(124,92,255,.2),transparent_70%)] text-gray-500"><Clapperboard size={32} /></div>}
                <div className="flex items-center justify-between gap-2"><p className="truncate font-mono text-xs text-gray-500">{v.id} - {rname(v.replica_id)}</p><Badge s={v.status} /></div>
                {it && <div className="flex flex-wrap gap-1.5 text-[11px]"><span className="rounded-full bg-white/5 px-2 py-0.5 text-gray-300">{it.language ? `language: ${it.language}` : `batch row ${it.row_index + 1}`}</span>
                  {Object.entries(it.variables).slice(0, 3).map(([k, val]) => <span key={k} className="rounded-full bg-mirage-amber/10 px-2 py-0.5 font-mono text-mirage-amber">{k}={val}</span>)}</div>}
                <p className="line-clamp-2 text-sm text-gray-300">{v.script}</p>
                {v.status === "queued" && <p className="flex items-start gap-2 rounded-xl border border-mirage-violet/25 bg-mirage-violet/10 p-3 text-xs leading-relaxed text-gray-300"><Cpu size={14} className="mt-0.5 shrink-0 text-mirage-violet" /><span>Queued: rendering needs a worker, a separate background process. Run this in the backend folder:<code className="mt-1.5 block rounded bg-black/40 px-2 py-1.5 font-mono text-[11px] text-gray-200">python workers/run_worker.py</code></span></p>}
                {v.status === "rendering" && <p className="text-xs text-mirage-cyan">Rendering now. This page refreshes automatically.</p>}
                {v.status === "error" && <p className="rounded-xl bg-mirage-rose/10 p-3 text-xs text-mirage-rose">Rendering failed. Check the worker logs and try again.</p>}
                {v.output_url && <div className="flex gap-2"><a className="btn-ghost !px-3 !py-1.5 text-xs" href={fileUrl(v.output_url)} target="_blank" rel="noreferrer"><ExternalLink size={13} />Open in new tab</a><CopyButton text={fileUrl(v.output_url)} label="Copy link" /></div>}
              </motion.div>);
          })}
        </div>
      )}
    </Shell>
  );
}
