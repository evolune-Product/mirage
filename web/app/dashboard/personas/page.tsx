"use client";
import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Mic, Pencil, Plus, UserRound } from "lucide-react";
import { api, Persona, Replica } from "@/lib/api";
import { Shell, Empty, SkeletonCards, Badge, toast } from "@/components/ui";
import PersonaDrawer from "@/components/persona/PersonaDrawer";

export default function Personas() {
  const [list, setList] = useState<Persona[] | null>(null); const [reps, setReps] = useState<Replica[]>([]);
  const [sel, setSel] = useState<Persona | null>(null); const [open, setOpen] = useState(false);
  const load = useCallback(async () => { try { setList(await api<Persona[]>("/v1/personas")); setReps(await api<Replica[]>("/v1/replicas")); } catch (x) { toast.error(x); setList((l) => l ?? []); } }, []);
  useEffect(() => { load(); }, [load]);
  const openNew = () => { setSel(null); setOpen(true); };
  const openEdit = (p: Persona) => { setSel(p); setOpen(true); };
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
      <PersonaDrawer open={open} onClose={() => setOpen(false)} persona={sel} reps={reps} onSaved={load} />
    </Shell>
  );
}
