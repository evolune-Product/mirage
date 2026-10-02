"use client";
import { useCallback, useEffect, useState } from "react";
import { api, Persona, Replica } from "@/lib/api";
import { Shell, Err } from "@/components/ui";
const empty = { name: "", system_prompt: "", knowledge: "", replica_id: "", tts_voice: "default", llm: "ollama/llama3.2:1b" };
export default function Personas() {
  const [list, setList] = useState<Persona[]>([]); const [reps, setReps] = useState<Replica[]>([]); const [f, setF] = useState(empty); const [editing, setEditing] = useState(""); const [err, setErr] = useState("");
  const load = useCallback(async () => { try { setList(await api<Persona[]>("/v1/personas")); setReps(await api<Replica[]>("/v1/replicas")); } catch (x) { setErr((x as Error).message); } }, []);
  useEffect(() => { load(); }, [load]);
  const set = (k: keyof typeof empty) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  async function save(e: React.FormEvent) { e.preventDefault(); setErr(""); try { await api("/v1/personas", { body: { ...f, replica_id: f.replica_id || null } }); setF(empty); setEditing(""); load(); } catch (x) { setErr((x as Error).message); } }
  return (<Shell title="Personas"><form onSubmit={save} className="card mb-6 space-y-3">
    {editing && <p className="text-xs text-amber-300">Editing a copy of {editing}. The API has no update endpoint yet, so saving creates a new persona.</p>}
    <div className="grid gap-3 md:grid-cols-2"><div><label className="label">Name</label><input className="input" required value={f.name} onChange={set("name")} /></div>
      <div><label className="label">Replica</label><select className="input" value={f.replica_id} onChange={set("replica_id")}><option value="">None</option>{reps.map(r => <option key={r.id} value={r.id}>{r.name} ({r.status})</option>)}</select></div>
      <div><label className="label">Voice</label><input className="input" value={f.tts_voice} onChange={set("tts_voice")} /></div><div><label className="label">LLM</label><input className="input" value={f.llm} onChange={set("llm")} /></div></div>
    <div><label className="label">System prompt</label><textarea className="input h-24" required value={f.system_prompt} onChange={set("system_prompt")} /></div>
    <div><label className="label">Knowledge</label><textarea className="input h-24" value={f.knowledge} onChange={set("knowledge")} /></div>
    <div className="flex gap-2"><button className="btn">{editing ? "Save as new" : "Create persona"}</button>{editing && <button type="button" className="btn-ghost" onClick={() => { setF(empty); setEditing(""); }}>Cancel</button>}</div></form><Err m={err} />
    <div className="space-y-2">{list.map(p => <div key={p.id} className="card flex items-start justify-between gap-4"><div className="min-w-0"><p className="font-medium">{p.name}</p><p className="text-xs text-gray-500">{p.id} / {p.llm} / {p.tts_voice}</p><p className="mt-1 truncate text-sm text-gray-400">{p.system_prompt}</p></div>
      <button className="btn-ghost" onClick={() => { setEditing(p.id); setF({ name: p.name, system_prompt: p.system_prompt, knowledge: p.knowledge, replica_id: p.replica_id || "", tts_voice: p.tts_voice, llm: p.llm }); window.scrollTo(0, 0); }}>Edit</button></div>)}{!list.length && <p className="text-gray-500">No personas yet.</p>}</div></Shell>);
}
