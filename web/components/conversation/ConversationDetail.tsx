"use client";
import { useEffect, useState } from "react";
import { CheckCircle2, Circle, Clock, Gauge, Wrench, MessageSquareQuote, Play } from "lucide-react";
import { api, Conversation, Turn, PersonaConfig, fmtDate, fmtDur } from "@/lib/api";
import { Modal, Badge, CopyButton, Skeleton, Section } from "@/components/ui";

type Transcript = { summary: string; status: string; turns: Turn[] };
type Metrics = { user_turns: number; agent_turns: number; avg_first_audio_ms: number | null; p95_first_audio_ms: number | null; interruptions: number; guardrail_hits: number; tool_calls: number };
type ObjRow = { name: string; completed: boolean; evidence: string; variables: Record<string, unknown> };
type ToolCall = { tool: string; arguments: Record<string, unknown>; result: string; ok: boolean; duration_ms: number; created_at: string };

const clock = (ms: number) => { const s = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const ms = (v: number | null | undefined) => (v == null ? "-" : v >= 1000 ? (v / 1000).toFixed(2) + " s" : Math.round(v) + " ms");

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3"><p className="text-[10px] uppercase tracking-wider text-gray-500">{label}</p><p className="mt-1 font-mono text-lg leading-none">{value}</p>{sub && <p className="mt-1 text-[11px] text-gray-500">{sub}</p>}</div>;
}

export default function ConversationDetail({ conv, personaName, onClose, onPlay }: { conv: Conversation | null; personaName: string; onClose: () => void; onPlay: (c: Conversation) => void }) {
  const [tr, setTr] = useState<Transcript | null>(null); const [m, setM] = useState<Metrics | null | undefined>(undefined);
  const [objs, setObjs] = useState<ObjRow[] | null>(null); const [cfgObjs, setCfgObjs] = useState<string[]>([]); const [calls, setCalls] = useState<ToolCall[]>([]);
  useEffect(() => {
    if (!conv) return; setTr(null); setM(undefined); setObjs(null); setCalls([]); setCfgObjs([]);
    const id = conv.id; let live = true;
    api<Transcript>(`/v1/conversations/${id}/transcript`).then((t) => live && setTr(t)).catch(() => live && setTr({ summary: "", status: conv.status, turns: [] }));
    api<Metrics>(`/v1/conversations/${id}/metrics`).then((x) => live && setM(x)).catch(() => live && setM(null));
    api<ObjRow[]>(`/v1/conversations/${id}/objectives`).then((x) => live && setObjs(x)).catch(() => live && setObjs([]));
    api<ToolCall[]>(`/v1/conversations/${id}/tool-calls`).then((x) => live && setCalls(x)).catch(() => {});
    api<PersonaConfig>(`/v1/personas/${conv.persona_id}/config`).then((c) => live && setCfgObjs(c.objectives.map((o) => o.name))).catch(() => {});
    return () => { live = false; };
  }, [conv]);
  const rows = (() => { const by = new Map((objs ?? []).map((o) => [o.name, o])); const names = [...new Set([...cfgObjs, ...by.keys()])];
    return names.map((n) => by.get(n) ?? { name: n, completed: false, evidence: "", variables: {} }); })();
  const text = tr?.turns.map((t) => `[${clock(t.t_ms)}] ${t.role === "user" ? "User" : "Agent"}: ${t.text}`).join("\n") ?? "";
  return (
    <Modal side wide open={!!conv} onClose={onClose} title="Conversation">
      {conv && (<>
        <div className="mb-5 flex flex-wrap items-center gap-x-3 gap-y-2">
          <Badge s={conv.status} /><span className="font-medium">{personaName}</span>
          <span className="inline-flex items-center gap-1 text-xs text-gray-500"><Clock size={12} />{fmtDur(conv.seconds_used)}</span>
          <span className="text-xs text-gray-500">{fmtDate(conv.started_at)}</span>
          <span className="w-full truncate font-mono text-[11px] text-gray-600 sm:w-auto sm:flex-1 sm:text-right">{conv.id}</span>
        </div>
        {conv.status !== "ended" && <button className="btn-grad mb-5" onClick={() => onPlay(conv)}><Play size={15} />Open in playground</button>}

        <Section title="Summary" icon={<MessageSquareQuote size={16} className="text-mirage-violet" />}>
          {tr === null ? <Skeleton className="h-14" /> : tr.summary ? <p className="rounded-xl border border-white/10 bg-white/[0.03] p-3.5 text-sm leading-relaxed text-gray-200" data-testid="summary">{tr.summary}</p>
            : <p className="rounded-xl border border-dashed border-white/15 p-3.5 text-sm text-gray-500">{conv.status === "ended" ? "No summary: the conversation had no spoken turns." : "A summary is written when the conversation ends."}</p>}
        </Section>

        <Section title="Metrics" icon={<Gauge size={16} className="text-mirage-cyan" />}>
          {m === undefined ? <Skeleton className="h-16" /> : m === null ? <p className="rounded-xl border border-dashed border-white/15 p-3.5 text-sm text-gray-500">Metrics are stored when the conversation ends.</p> : (
            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
              <Tile label="First audio (avg)" value={ms(m.avg_first_audio_ms)} sub={m.p95_first_audio_ms != null ? `p95 ${ms(m.p95_first_audio_ms)}` : undefined} />
              <Tile label="Turns" value={`${m.user_turns} / ${m.agent_turns}`} sub="user / agent" />
              <Tile label="Interruptions" value={String(m.interruptions)} />
              <Tile label="Tool calls" value={String(m.tool_calls)} sub={m.guardrail_hits ? `${m.guardrail_hits} guardrail hits` : undefined} />
            </div>)}
        </Section>

        {rows.length > 0 && (
          <Section title="Objectives" hint="Marked complete by the judge model during the conversation.">
            <ul className="space-y-2">{rows.map((o) => (
              <li key={o.name} className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <div className="flex items-center gap-2">{o.completed ? <CheckCircle2 size={16} className="text-mirage-mint" /> : <Circle size={16} className="text-gray-600" />}
                  <span className="font-mono text-sm">{o.name}</span><span className={`ml-auto text-xs ${o.completed ? "text-mirage-mint" : "text-gray-500"}`}>{o.completed ? "completed" : "not completed"}</span></div>
                {o.evidence && <p className="mt-1.5 text-xs italic text-gray-400">&ldquo;{o.evidence}&rdquo;</p>}
                {Object.keys(o.variables).length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{Object.entries(o.variables).map(([k, v]) => <span key={k} className="rounded-full bg-mirage-violet/15 px-2 py-0.5 font-mono text-[11px] text-mirage-violet">{k}={String(v)}</span>)}</div>}
              </li>))}</ul>
          </Section>)}

        {calls.length > 0 && (
          <Section title="Tool calls" icon={<Wrench size={16} className="text-mirage-amber" />}>
            <ul className="space-y-2">{calls.map((c, i) => (
              <li key={i} className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-xs">
                <p className="flex items-center gap-2"><span className="font-mono text-sm text-white">{c.tool}</span><Badge s={c.ok ? "completed" : "error"} /><span className="ml-auto text-gray-500">{Math.round(c.duration_ms)} ms</span></p>
                <p className="mt-1.5 break-all font-mono text-gray-400">{JSON.stringify(c.arguments)}</p>{c.result && <p className="mt-1 line-clamp-3 break-all text-gray-300">{c.result}</p>}
              </li>))}</ul>
          </Section>)}

        <Section title="Transcript" action={text ? <CopyButton text={text} label="Copy" /> : undefined}>
          {tr === null ? <Skeleton className="h-32" /> : tr.turns.length === 0 ? <p className="rounded-xl border border-dashed border-white/15 p-3.5 text-sm text-gray-500">No turns were recorded for this conversation.</p> : (
            <ol className="space-y-2.5" data-testid="transcript">{tr.turns.map((t) => (
              <li key={t.seq} className={`flex ${t.role === "user" ? "justify-end" : ""}`}>
                <div className={`max-w-[88%] rounded-2xl px-3.5 py-2.5 text-sm ${t.role === "user" ? "rounded-br-md bg-mirage-violet/20 text-gray-100" : "rounded-bl-md border border-white/10 bg-white/[0.04] text-gray-200"}`}>
                  <p className="mb-1 flex flex-wrap items-center gap-x-2 text-[10px] uppercase tracking-wider text-gray-500"><span>{t.role === "user" ? "User" : "Agent"}</span><span className="font-mono">{clock(t.t_ms)}</span>
                    {t.first_audio_ms != null && <span className="rounded bg-mirage-cyan/10 px-1.5 py-0.5 font-mono normal-case text-mirage-cyan">first audio {ms(t.first_audio_ms)}</span>}
                    {t.interrupted && <span className="rounded bg-mirage-amber/10 px-1.5 py-0.5 normal-case text-mirage-amber">interrupted</span>}</p>
                  {t.text}
                </div>
              </li>))}</ol>)}
        </Section>
      </>)}
    </Modal>
  );
}
