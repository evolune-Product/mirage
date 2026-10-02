"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Shell, Err } from "@/components/ui";
export default function Overview() {
  const [c, setC] = useState<number | null>(null); const [n, setN] = useState<Record<string, number>>({}); const [err, setErr] = useState("");
  useEffect(() => { (async () => { try {
    setC((await api<{ credits_seconds: number }>("/v1/usage")).credits_seconds);
    const [r, p] = await Promise.all([api<unknown[]>("/v1/replicas"), api<unknown[]>("/v1/personas")]); setN({ Replicas: r.length, Personas: p.length });
  } catch (x) { setErr((x as Error).message); } })(); }, []);
  const pct = c === null ? 0 : Math.min(100, (c / 600) * 100);
  return (<Shell title="Overview"><div className="grid gap-4 md:grid-cols-3">
    <div className="card md:col-span-3"><p className="label">Credits remaining</p><p className="text-3xl font-bold">{c === null ? "..." : `${c}s`} <span className="text-base font-normal text-gray-400">{c !== null && `(${(c / 60).toFixed(1)} min)`}</span></p>
      <div className="mt-3 h-2 rounded bg-white/10"><div className="h-2 rounded bg-accent" style={{ width: pct + "%" }} /></div></div>
    {Object.entries(n).map(([k, v]) => <div key={k} className="card"><p className="label">{k}</p><p className="text-2xl font-bold">{v}</p></div>)}</div><Err m={err} /></Shell>);
}
