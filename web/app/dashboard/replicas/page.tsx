"use client";
import { useCallback, useEffect, useState } from "react";
import { api, Replica } from "@/lib/api";
import { Shell, Err, Badge } from "@/components/ui";
export default function Replicas() {
  const [list, setList] = useState<Replica[]>([]); const [name, setName] = useState(""); const [url, setUrl] = useState(""); const [err, setErr] = useState("");
  const load = useCallback(async () => { try { setList(await api<Replica[]>("/v1/replicas")); } catch (x) { setErr((x as Error).message); } }, []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);
  async function create(e: React.FormEvent) { e.preventDefault(); setErr(""); try { await api("/v1/replicas", { body: { name, train_video_url: url } }); setName(""); setUrl(""); load(); } catch (x) { setErr((x as Error).message); } }
  const [cons, setCons] = useState<{ rid: string; cid: string; phrase: string } | null>(null); const [said, setSaid] = useState(""); const [who, setWho] = useState("");
  async function startConsent(rid: string) { setErr(""); try { const c = await api<{ challenge_id: string; phrase: string }>(`/v1/replicas/${rid}/consent/challenge`, { method: "POST", body: {} }); setCons({ rid, cid: c.challenge_id, phrase: c.phrase }); setSaid(""); } catch (x) { setErr((x as Error).message); } }
  async function submitConsent() { if (!cons) return; setErr(""); try { await api(`/v1/replicas/${cons.rid}/consent`, { body: { challenge_id: cons.cid, speaker_name: who, audio_url: "dashboard://typed-confirmation", transcript: said } }); setCons(null); load(); } catch (x) { setErr((x as Error).message); } }
  return (<Shell title="Replicas"><form onSubmit={create} className="card mb-6 grid gap-3 md:grid-cols-[1fr_2fr_auto] md:items-end">
    <div><label className="label">Name</label><input className="input" required value={name} onChange={e => setName(e.target.value)} /></div>
    <div><label className="label">Training video URL</label><input className="input" type="url" required value={url} onChange={e => setUrl(e.target.value)} /></div><button className="btn">Create</button></form><Err m={err} />
    {cons && <div className="card mb-6"><p className="label">Consent for {cons.rid}</p><p className="mb-3 text-sm text-gray-300">The person in the training video must confirm. Read this phrase aloud, then type it exactly below:</p>
      <p className="mb-3 rounded-lg bg-white/5 p-3 text-sm">{cons.phrase}</p>
      <div className="grid gap-3 md:grid-cols-[1fr_2fr_auto] md:items-end"><div><label className="label">Your name</label><input className="input" value={who} onChange={e => setWho(e.target.value)} /></div>
      <div><label className="label">Phrase you said</label><input className="input" value={said} onChange={e => setSaid(e.target.value)} /></div><button className="btn" disabled={!who || !said} onClick={submitConsent}>Confirm consent</button></div></div>}
    <div className="space-y-2">{list.map(r => <div key={r.id} className="card flex items-center justify-between"><div><p className="font-medium">{r.name}</p><p className="text-xs text-gray-500">{r.id}</p></div><div className="flex items-center gap-3">{r.status === "awaiting_consent" && <button className="btn-ghost" onClick={() => startConsent(r.id)}>Give consent</button>}<Badge s={r.status} /></div></div>)}{!list.length && <p className="text-gray-500">No replicas yet.</p>}</div></Shell>);
}
