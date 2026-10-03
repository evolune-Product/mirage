"use client";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Check, Sparkles, Target, Wrench } from "lucide-react";
import { api, errText, Instantiated, Replica, TemplateDetail, TemplateSummary } from "@/lib/api";
import { Field, Skeleton, Spinner, Toggle, toast } from "@/components/ui";
import { Callout, Segmented } from "@/components/kit";

/** Static fallback used when the server has no /v1/templates (older backends). Creating from these makes a plain persona. */
export const FALLBACK: TemplateSummary[] = [
  { id: "fallback-sales", name: "Sales assistant", niche: "sales", summary: "Friendly product expert that answers from your docs and collects contact details.", version: 1, language: "en", suggested_llm: "ollama/llama3.2:3b", tools: [], objectives: [], lead_fields: [], knowledge_docs: 0, safety_notes: "" },
  { id: "fallback-support", name: "Support agent", niche: "support", summary: "Answers customer questions from your help centre and hands off when unsure.", version: 1, language: "en", suggested_llm: "ollama/llama3.2:3b", tools: [], objectives: [], lead_fields: [], knowledge_docs: 0, safety_notes: "" },
  { id: "fallback-tutor", name: "Tutor", niche: "education", summary: "Patient explainer that teaches step by step.", version: 1, language: "en", suggested_llm: "ollama/llama3.2:3b", tools: [], objectives: [], lead_fields: [], knowledge_docs: 0, safety_notes: "" },
];
const FALLBACK_PROMPT: Record<string, string> = { "fallback-sales": "You are a friendly, honest sales assistant. Answer only from the knowledge provided and ask for contact details when the visitor is interested.", "fallback-support": "You are a calm support agent. Answer from the knowledge provided; if you do not know, say so and offer a human.", "fallback-tutor": "You are a patient tutor. Explain step by step, check understanding, and keep answers short." };

export function useTemplates() {
  const [list, setList] = useState<TemplateSummary[] | null>(null); const [fallback, setFallback] = useState(false);
  useEffect(() => { api<{ templates: TemplateSummary[] }>("/v1/templates").then((r) => setList(r.templates)).catch(() => { setList(FALLBACK); setFallback(true); }); }, []);
  return { list, fallback };
}

export default function TemplatePicker({ replicas, onCreated, defaultReplicaId, compact, initialId }: { initialId?: string; replicas: Replica[]; onCreated: (r: Instantiated) => void; defaultReplicaId?: string; compact?: boolean }) {
  const { list, fallback } = useTemplates(); const [niche, setNiche] = useState("all"); const [sel, setSel] = useState<TemplateSummary | null>(null);
  const [d, setD] = useState<TemplateDetail | null>(null); const [vars, setVars] = useState<Record<string, string>>({}); const [name, setName] = useState(""); const [rid, setRid] = useState(defaultReplicaId ?? "");
  const [sample, setSample] = useState(true); const [lead, setLead] = useState(true); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  useEffect(() => { if (defaultReplicaId) setRid(defaultReplicaId); }, [defaultReplicaId]);
  useEffect(() => { if (initialId && list && !sel) { const t = list.find((x) => x.id === initialId); if (t) setSel(t); } }, [initialId, list]); // eslint-disable-line react-hooks/exhaustive-deps
  const niches = useMemo(() => ["all", ...Array.from(new Set((list ?? []).map((t) => t.niche)))], [list]);
  useEffect(() => {
    setD(null); setErr(""); if (!sel || fallback) return;
    api<TemplateDetail>(`/v1/templates/${sel.id}`).then((t) => { setD(t); setVars(t.variables); setName(t.persona_name); }).catch((x) => setErr(errText(x)));
  }, [sel, fallback]);
  async function create() {
    if (!sel) return; setBusy(true); setErr("");
    try {
      if (fallback) {
        const p = await api<{ id: string; name: string; llm: string }>("/v1/personas", { body: { name: name || sel.name, system_prompt: FALLBACK_PROMPT[sel.id], replica_id: rid || null, llm: sel.suggested_llm } });
        onCreated({ persona_id: p.id, persona: p, warnings: [], next_steps: ["Add knowledge documents to the persona so it can answer your questions."], knowledge_docs: [], lead_capture: { enabled: false } });
      } else {
        const r = await api<Instantiated>(`/v1/templates/${sel.id}/instantiate`, { body: { name, variables: vars, replica_id: rid || undefined, include_sample_knowledge: sample, enable_lead_capture: lead } });
        onCreated(r);
      }
      toast.success("Persona created from the template.");
    } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  if (list === null) return <div className="grid gap-3 sm:grid-cols-2"><Skeleton className="h-32" /><Skeleton className="h-32" /></div>;
  if (!sel) return (
    <div>
      {fallback && <div className="mb-3"><Callout tone="warn" title="Using built-in basics">This server has no template library, so these are simple starting points instead of full ready-made agents.</Callout></div>}
      <div className="mb-4"><Segmented size="sm" label="Niche" value={niche} onChange={setNiche} options={niches.map((n) => ({ id: n, label: n === "all" ? "All" : n.replace("-", " ") }))} /></div>
      <ul className={`grid gap-3 ${compact ? "sm:grid-cols-2" : "sm:grid-cols-2 xl:grid-cols-3"}`} data-testid="template-grid">
        {list.filter((t) => niche === "all" || t.niche === niche).map((t) => (
          <li key={t.id}><button type="button" onClick={() => setSel(t)} data-template={t.id} className="flex h-full w-full flex-col rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-left transition hover:border-mirage-violet/50 hover:bg-white/[0.07]">
            <span className="mb-2 w-fit rounded-full bg-mirage-violet/15 px-2 py-0.5 text-[10px] uppercase tracking-wide text-mirage-violet">{t.niche.replace("-", " ")}</span>
            <span className="font-medium">{t.name}</span><span className="mt-1 flex-1 text-xs leading-relaxed text-gray-400">{t.summary}</span>
            <span className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-gray-500">{t.objectives.length > 0 && <span className="inline-flex items-center gap-1"><Target size={11} />{t.objectives.length} goals</span>}{t.tools.length > 0 && <span className="inline-flex items-center gap-1"><Wrench size={11} />{t.tools.length} tool{t.tools.length > 1 ? "s" : ""}</span>}{t.safety_notes && <span className="text-mirage-amber">safety note</span>}</span>
          </button></li>))}
      </ul>
    </div>);
  return (
    <div className="space-y-4" data-testid="template-config">
      <button type="button" className="inline-flex items-center gap-1.5 text-xs text-gray-400 hover:text-white" onClick={() => setSel(null)}><ArrowLeft size={13} />All templates</button>
      <div><h3 className="font-display text-3xl">{sel.name}</h3><p className="mt-1 text-sm text-gray-400">{sel.summary}</p></div>
      {!fallback && !d && !err && <Skeleton className="h-40" />}
      {d?.safety_notes && <Callout tone="warn" title="Read this first">{d.safety_notes}</Callout>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Persona name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} aria-label="Persona name" placeholder={sel.name} /></Field>
        <Field label="Replica (face)" hint="Optional: pick later, a persona works as voice only."><select className="input" aria-label="Replica" value={rid} onChange={(e) => setRid(e.target.value)}><option value="">None (voice only)</option>{replicas.map((r) => <option key={r.id} value={r.id}>{r.name} ({r.status})</option>)}</select></Field>
      </div>
      {d && Object.keys(vars).length > 0 && <div className="rounded-xl bg-white/[0.03] p-4"><p className="mb-3 text-sm font-medium">Make it yours</p><div className="grid gap-3 sm:grid-cols-2">{Object.entries(vars).map(([k, v]) => <Field key={k} label={k.replace(/_/g, " ")}><input className="input" aria-label={`Variable ${k}`} value={v} onChange={(e) => setVars({ ...vars, [k]: e.target.value })} /></Field>)}</div></div>}
      {d && <div className="rounded-xl border border-white/10 p-4 text-sm"><p className="label">Opening line</p><p className="italic text-gray-300">&ldquo;{Object.entries(vars).reduce((g, [k, v]) => g.split(`{{${k}}}`).join(v), d.greeting)}&rdquo;</p>
        {d.sample_questions.length > 0 && <><p className="label mt-3">Try asking</p><ul className="flex flex-wrap gap-1.5">{d.sample_questions.slice(0, 4).map((q, i) => { const t = typeof q === "string" ? q : (q as { q?: string }).q ?? ""; return <li key={i} className="rounded-full bg-white/5 px-2.5 py-1 text-xs text-gray-300">{t}</li>; })}</ul></>}</div>}
      {d && <div className="space-y-3">
        <Toggle checked={sample} onChange={setSample} label={`Include sample knowledge (${d.knowledge.length} documents)`} hint="Documents titled SAMPLE describe a fictional company with fake data. Replace them with your own before going live." />
        <Toggle checked={lead} onChange={setLead} label="Collect leads (name, email, phone) with consent" hint="The agent asks politely, says how data is used, and saves it under Leads." /></div>}
      {err && <Callout tone="bad">{err}</Callout>}
      <button type="button" className="btn-grad w-full" disabled={busy || (!fallback && !d)} onClick={create}>{busy ? <Spinner /> : <Sparkles size={15} />}Create persona</button>
    </div>
  );
}

export function CreatedSummary({ r }: { r: Instantiated }) {
  return (
    <div className="space-y-3" data-testid="template-created">
      <p className="flex items-center gap-2 text-mirage-mint"><Check size={16} />Created <b className="text-white">{r.persona.name}</b></p>
      {r.warnings.map((w, i) => <Callout key={i} tone="warn">{String(w)}</Callout>)}
      {r.next_steps.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm text-gray-300">{r.next_steps.map((n, i) => <li key={i}>{String(n)}</li>)}</ul>}
    </div>
  );
}
