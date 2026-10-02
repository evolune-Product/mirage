"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, setKey } from "@/lib/api";
import { Err } from "@/components/ui";
export default function Signup() {
  const [email, setEmail] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false); const r = useRouter();
  async function go(e: React.FormEvent) { e.preventDefault(); setBusy(true); setErr("");
    try { const j = await api<{ api_key: string }>("/v1/signup", { body: { email }, key: "" }); setKey(j.api_key); r.push("/dashboard"); }
    catch (x) { setErr(String((x as Error).message)); } finally { setBusy(false); } }
  return (<div className="mx-auto mt-24 max-w-sm px-6"><form onSubmit={go} className="card">
    <h1 className="mb-1 text-xl font-semibold">Get your API key</h1><p className="mb-4 text-sm text-gray-400">Includes 600 free seconds. The key is stored in this browser only.</p>
    <label className="label">Email</label><input className="input mb-4" type="email" required value={email} onChange={e => setEmail(e.target.value)} />
    <button className="btn w-full" disabled={busy}>{busy ? "Creating..." : "Sign up"}</button><Err m={err} />
    <p className="mt-4 text-xs text-gray-500">Already have a key? <a className="text-accent" href="/dashboard/keys">Paste it here</a>.</p></form></div>);
}
