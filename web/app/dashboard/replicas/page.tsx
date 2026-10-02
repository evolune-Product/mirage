"use client";
import { useCallback, useEffect, useState } from "react";
import { api, Replica } from "@/lib/api";
import { Shell, Err, Badge } from "@/components/ui";
export default function Replicas() {
  const [list, setList] = useState<Replica[]>([]); const [name, setName] = useState(""); const [url, setUrl] = useState(""); const [err, setErr] = useState("");
  const load = useCallback(async () => { try { setList(await api<Replica[]>("/v1/replicas")); } catch (x) { setErr((x as Error).message); } }, []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);
  async function create(e: React.FormEvent) { e.preventDefault(); setErr(""); try { await api("/v1/replicas", { body: { name, train_video_url: url } }); setName(""); setUrl(""); load(); } catch (x) { setErr((x as Error).message); } }
  return (<Shell title="Replicas"><form onSubmit={create} className="card mb-6 grid gap-3 md:grid-cols-[1fr_2fr_auto] md:items-end">
    <div><label className="label">Name</label><input className="input" required value={name} onChange={e => setName(e.target.value)} /></div>
    <div><label className="label">Training video URL</label><input className="input" type="url" required value={url} onChange={e => setUrl(e.target.value)} /></div><button className="btn">Create</button></form><Err m={err} />
    <div className="space-y-2">{list.map(r => <div key={r.id} className="card flex items-center justify-between"><div><p className="font-medium">{r.name}</p><p className="text-xs text-gray-500">{r.id}</p></div><Badge s={r.status} /></div>)}{!list.length && <p className="text-gray-500">No replicas yet.</p>}</div></Shell>);
}
