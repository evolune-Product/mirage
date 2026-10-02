"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, ReactNode } from "react";
import { clearKey, getKey } from "@/lib/api";

export function Badge({ s }: { s: string }) {
  const c = s === "ready" || s === "active" ? "text-emerald-300 bg-emerald-500/10" : s === "error" ? "text-red-300 bg-red-500/10" : "text-amber-300 bg-amber-500/10";
  return <span className={`rounded-full px-2 py-0.5 text-xs ${c}`}>{s}</span>;
}
export const Err = ({ m }: { m: string }) => m ? <p className="mt-3 rounded-lg bg-red-500/10 p-3 text-sm text-red-300">{m}</p> : null;

const nav = [["/dashboard","Overview"],["/dashboard/replicas","Replicas"],["/dashboard/personas","Personas"],["/dashboard/conversations","Conversations"],["/dashboard/videos","Videos"],["/dashboard/billing","Billing"],["/dashboard/keys","API keys & docs"]];

export function Shell({ title, children }: { title: string; children: ReactNode }) {
  const path = usePathname(); const router = useRouter(); const [ok, setOk] = useState(false);
  useEffect(() => { if (!getKey()) router.replace("/signup"); else setOk(true); }, [router]);
  if (!ok) return <div className="p-10 text-gray-400">Loading...</div>;
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="border-b border-line p-4 md:w-56 md:border-b-0 md:border-r">
        <Link href="/" className="mb-4 block text-lg font-semibold">Mirage</Link>
        <nav className="flex gap-1 overflow-x-auto md:flex-col">
          {nav.map(([h, l]) => <Link key={h} href={h} className={`whitespace-nowrap rounded-lg px-3 py-2 text-sm ${path === h ? "bg-white/10 text-white" : "text-gray-400 hover:bg-white/5"}`}>{l}</Link>)}
          <button className="rounded-lg px-3 py-2 text-left text-sm text-gray-500 hover:bg-white/5" onClick={() => { clearKey(); router.push("/"); }}>Sign out</button>
        </nav>
      </aside>
      <main className="flex-1 p-6 md:p-10"><h1 className="mb-6 text-2xl font-semibold">{title}</h1>{children}</main>
    </div>
  );
}
