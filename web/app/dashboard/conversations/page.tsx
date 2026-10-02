"use client";
import { useEffect, useState } from "react";
import { API_URL, api, Conversation, Persona, getKey, loadIds, saveId } from "@/lib/api";
import { Shell, Err, Badge } from "@/components/ui";
export default function Conversations() {
  const [ps, setPs] = useState<Persona[]>([]); const [pid, setPid] = useState(""); const [cur, setCur] = useState<Conversation | null>(null); const [err, setErr] = useState(""); const [past, setPast] = useState<string[]>([]);
  useEffect(() => { setPast(loadIds("conversations")); api<Persona[]>("/v1/personas").then(l => { setPs(l); if (l[0]) setPid(l[0].id); }).catch(x => setErr(x.message)); }, []);
  async function start() { setErr(""); try { const c = await api<Conversation>("/v1/conversations", { body: { persona_id: pid } }); saveId("conversations", c.id); setPast(loadIds("conversations")); setCur(c); } catch (x) { setErr((x as Error).message); } }
  async function end() { if (!cur) return; try { setCur(await api<Conversation>(`/v1/conversations/${cur.id}/end`, { method: "POST", body: {} })); } catch (x) { setErr((x as Error).message); } }
  const src = cur ? `${API_URL}/static/playground.html?cid=${cur.id}&api_key=${encodeURIComponent(getKey())}&api=${encodeURIComponent(API_URL)}` : "";
  return (<Shell title="Conversations"><div className="card mb-6 flex flex-wrap items-end gap-3"><div className="min-w-60 flex-1"><label className="label">Persona</label>
    <select className="input" value={pid} onChange={e => setPid(e.target.value)}>{ps.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
    <button className="btn" disabled={!pid} onClick={start}>Start conversation</button></div>{!ps.length && <p className="text-gray-500">Create a persona first.</p>}<Err m={err} />
    {cur && <div className="card"><div className="mb-3 flex items-center justify-between"><p className="text-sm">{cur.id} <Badge s={cur.status} /> {cur.status === "ended" && `${cur.seconds_used}s used`}</p>{cur.status !== "ended" && <button className="btn-ghost" onClick={end}>End</button>}</div>
      <iframe src={src} className="h-[560px] w-full rounded-lg border border-line bg-black" allow="camera; microphone; autoplay" title="Playground" /></div>}
    {past.length > 0 && <div className="mt-6"><p className="label">Recent conversation ids</p><p className="text-sm text-gray-400">{past.join(", ")}</p></div>}</Shell>);
}
