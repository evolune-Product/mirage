"use client";
import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { FileText, Loader2, Mic, Pencil, Plus, Trash2, UserRound, BrainCircuit } from "lucide-react";
import { api, Persona, Replica } from "@/lib/api";
import { Shell, Empty, Modal, SkeletonCards, Badge, toast } from "@/components/ui";

const empty = { name: "", system_prompt: "", knowledge: "", replica_id: "", tts_voice: "default", llm: "ollama/llama3.2:1b" };
type Doc = { id: string; title: string; source: string; n_chunks: number; created_at: string };

function Knowledge({ pid }: { pid: string }) {
  const [docs, setDocs] = useState<Doc[] | null>(null); const [title, setTitle] = useState(""); const [text, setText] = useState(""); const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { try { setDocs(await api<Doc[]>(`/v1/personas/${pid}/knowledge`)); } catch (x) { toast.error(x); setDocs([]); } }, [pid]);
  useEffect(() => { setDocs(null); load(); }, [load]);
  async function add(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try { await api(`/v1/personas/${pid}/knowledge/text`, { body: { title, text } }); setTitle(""); setText(""); toast.success("Document added to knowledge."); load(); }
    catch (x) { toast.error(x); } finally { setBusy(false); }
  }
  async function del(id: string) {
    try { await api(`/v1/personas/${pid}/knowledge/${id}`, { method: "DELETE" }); load(); } catch (x) { toast.error(x); }
  }
  return (
    <div className="mt-8 border-t border-white/10 pt-6">
      <h3 className="flex items-center gap-2 font-medium"><BrainCircuit size={17} className="text-mirage-violet" />Knowledge</h3>
      <p className="mb-4 mt-1 text-xs text-gray-400">The persona answers from these documents. Paste pricing, FAQs, policies, anything it should know.</p>
      {docs === null ? <div className="h-12 animate-pulse rounded-xl bg-white/5" /> : docs.length === 0 ? (
        <div className="mb-4 flex items-center gap-3 rounded-xl border border-dashed border-white/15 p-4 text-sm text-gray-500"><FileText size={18} />No documents yet.</div>
      ) : (
        <ul className="mb-4 space-y-2">{docs.map((d) => (
          <li key={d.id} className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-3.5 py-2.5">
            <FileText size={16} className="shrink-0 text-mirage-cyan" />
            <div className="min-w-0 flex-1"><p className="truncate text-sm">{d.title}</p><p className="text-xs text-gray-500">{d.source} - {d.n_chunks} chunks</p></div>
            <button aria-label={`Delete ${d.title}`} onClick={() => del(d.id)} className="rounded-lg p-1.5 text-gray-500 hover:bg-mirage-rose/10 hover:text-mirage-rose"><Trash2 size={15} /></button>
          </li>))}</ul>
      )}
      <form onSubmit={add} className="space-y-3 rounded-xl bg-white/[0.03] p-3.5">
        <input className="input" placeholder="Title, e.g. Pricing" required value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea className="input h-24" placeholder="Paste the text..." required value={text} onChange={(e) => setText(e.target.value)} />
        <button className="btn" disabled={busy}>{busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}Add document</button>
      </form>
    </div>
  );
}

export default function Personas() {
  const [list, setList] = useState<Persona[] | null>(null); const [reps, setReps] = useState<Replica[]>([]);
  const [f, setF] = useState(empty); const [editing, setEditing] = useState<string | null>(null); const [open, setOpen] = useState(false); const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { try { setList(await api<Persona[]>("/v1/personas")); setReps(await api<Replica[]>("/v1/replicas")); } catch (x) { toast.error(x); setList((l) => l ?? []); } }, []);
  useEffect(() => { load(); }, [load]);
  const set = (k: keyof typeof empty) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const openNew = () => { setF(empty); setEditing(null); setOpen(true); };
  const openEdit = (p: Persona) => { setEditing(p.id); setF({ name: p.name, system_prompt: p.system_prompt, knowledge: p.knowledge || "", replica_id: p.replica_id || "", tts_voice: p.tts_voice, llm: p.llm }); setOpen(true); };

  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    const body = { ...f, replica_id: f.replica_id || null };
    try {
      if (editing) { await api(`/v1/personas/${editing}`, { method: "PUT", body }); toast.success("Persona saved."); }
      else { const p = await api<Persona>("/v1/personas", { body }); setEditing(p.id); toast.success("Persona created. Now add some knowledge below."); }
      load();
    } catch (x) { toast.error(x); } finally { setBusy(false); }
  }
  const repName = (id: string | null) => { const r = reps.find((x) => x.id === id); return r ? r.name : null; };
  const newBtn = <button className="btn-grad" onClick={openNew}><Plus size={16} />New persona</button>;

  return (
    <Shell title="Personas" subtitle="A persona is the mind behind the face: personality, voice, model and the knowledge it answers from." action={newBtn}>
      {list === null ? <SkeletonCards /> : list.length === 0 ? (
        <Empty kind="persona" title="No personas yet" hint="Give your agent a personality, link it to a replica and teach it with your own documents." action={newBtn} />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {list.map((p, i) => (
            <motion.button key={p.id} onClick={() => openEdit(p)} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}
              className="card group flex flex-col text-left transition hover:-translate-y-0.5 hover:border-white/25 hover:bg-ink-3/80">
              <div className="flex items-start gap-3">
                <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-mirage-gradient text-white"><UserRound size={20} /></span>
                <div className="min-w-0 flex-1"><p className="truncate font-medium">{p.name}</p><p className="truncate font-mono text-xs text-gray-500">{p.id}</p></div>
                <Pencil size={15} className="text-gray-600 transition group-hover:text-white" />
              </div>
              <p className="mt-3 line-clamp-3 text-sm text-gray-400">{p.system_prompt}</p>
              <div className="mt-4 flex flex-wrap gap-1.5 text-xs">
                <span className="rounded-full bg-white/5 px-2.5 py-1 text-gray-300">{p.llm}</span>
                <span className="inline-flex items-center gap-1 rounded-full bg-white/5 px-2.5 py-1 text-gray-300"><Mic size={11} />{p.tts_voice}</span>
                {repName(p.replica_id) ? <span className="rounded-full bg-mirage-violet/15 px-2.5 py-1 text-mirage-violet">{repName(p.replica_id)}</span> : <Badge s="no replica" />}
              </div>
            </motion.button>
          ))}
        </div>
      )}
      <Modal side open={open} onClose={() => setOpen(false)} title={editing ? "Edit persona" : "New persona"}>
        <form onSubmit={save} className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div><label className="label">Name</label><input className="input" required value={f.name} onChange={set("name")} /></div>
            <div><label className="label">Replica</label><select className="input" value={f.replica_id} onChange={set("replica_id")}><option value="">None</option>{reps.map((r) => <option key={r.id} value={r.id}>{r.name} ({r.status})</option>)}</select></div>
            <div><label className="label">Voice</label><input className="input" value={f.tts_voice} onChange={set("tts_voice")} /></div>
            <div><label className="label">LLM</label><input className="input" value={f.llm} onChange={set("llm")} /></div>
          </div>
          <div><label className="label">System prompt</label><textarea className="input h-28" required placeholder="You are a friendly sales rep who..." value={f.system_prompt} onChange={set("system_prompt")} /></div>
          <div><label className="label">Quick notes (always in context)</label><textarea className="input h-20" value={f.knowledge} onChange={set("knowledge")} /></div>
          <button className="btn-grad w-full" disabled={busy}>{busy && <Loader2 size={15} className="animate-spin" />}{editing ? "Save changes" : "Create persona"}</button>
        </form>
        {editing ? <Knowledge pid={editing} /> : <p className="mt-6 rounded-xl border border-dashed border-white/15 p-4 text-xs text-gray-500">Create the persona first, then you can upload knowledge documents here.</p>}
      </Modal>
    </Shell>
  );
}
