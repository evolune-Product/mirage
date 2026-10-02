"use client";
import { useCallback, useEffect, useState } from "react";
import { API_URL, api, Replica, Video, loadIds, saveId } from "@/lib/api";
import { Shell, Err, Badge } from "@/components/ui";
const fileUrl = (u: string) => (u.startsWith("http") ? u : API_URL + u);
export default function Videos() {
  const [reps, setReps] = useState<Replica[]>([]); const [rid, setRid] = useState(""); const [script, setScript] = useState(""); const [vids, setVids] = useState<Video[]>([]); const [err, setErr] = useState("");
  const load = useCallback(async () => { const out: Video[] = []; for (const id of loadIds("videos")) { try { out.push(await api<Video>(`/v1/videos/${id}`)); } catch {} } setVids(out); }, []);
  useEffect(() => { api<Replica[]>("/v1/replicas").then(l => { const ok = l.filter(r => r.status === "ready"); setReps(ok); if (ok[0]) setRid(ok[0].id); }).catch(x => setErr(x.message)); load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);
  async function create(e: React.FormEvent) { e.preventDefault(); setErr(""); try { const v = await api<Video>("/v1/videos", { body: { replica_id: rid, script } }); saveId("videos", v.id); setScript(""); load(); } catch (x) { setErr((x as Error).message); } }
  return (<Shell title="Videos"><form onSubmit={create} className="card mb-6 space-y-3"><div><label className="label">Replica (ready only)</label>
    <select className="input" value={rid} onChange={e => setRid(e.target.value)}>{reps.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></div>
    <div><label className="label">Script</label><textarea className="input h-28" required value={script} onChange={e => setScript(e.target.value)} /></div><button className="btn" disabled={!rid}>Generate video</button>{!reps.length && <p className="text-sm text-gray-500">No ready replicas yet.</p>}</form><Err m={err} />
    <div className="space-y-2">{vids.map(v => <div key={v.id} className="card"><div className="flex items-center justify-between"><p className="text-sm font-medium">{v.id}</p><Badge s={v.status} /></div><p className="mt-1 truncate text-sm text-gray-400">{v.script}</p>{v.output_url && <><video className="mt-3 w-full max-w-md rounded-lg" controls src={fileUrl(v.output_url)} /><a className="mt-1 block text-sm text-accent" href={fileUrl(v.output_url)} target="_blank">Open video in new tab</a></>}</div>)}{!vids.length && <p className="text-gray-500">No videos yet. The API has no list endpoint, so videos created from this browser are tracked locally.</p>}</div></Shell>);
}
