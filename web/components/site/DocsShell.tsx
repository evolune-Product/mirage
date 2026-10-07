"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Search } from "lucide-react";

type Idx = { id: string; title: string; text: string; slug: string; page: string };
type P = { slug: string; title: string };

export default function DocsShell({ pages, index, active, toc, children }: { pages: readonly P[]; index: Idx[]; active: string; toc: { id: string; title: string }[]; children: React.ReactNode }) {
  const [q, setQ] = useState("");
  const res = useMemo(() => {
    const t = q.trim().toLowerCase(); if (t.length < 2) return [];
    return index.map((s) => { const hay = (s.title + " " + s.text).toLowerCase(); const pos = hay.indexOf(t); return { s, score: s.title.toLowerCase().includes(t) ? 2 : pos >= 0 ? 1 : 0, pos }; }).filter((r) => r.score).sort((a, b) => b.score - a.score).slice(0, 8);
  }, [q, index]);
  return (
    <div className="mx-auto grid w-full max-w-7xl gap-10 px-5 py-10 lg:grid-cols-[240px_minmax(0,1fr)_200px] lg:py-14">
      <aside className="lg:sticky lg:top-24 lg:self-start">
        <label className="relative block"><span className="sr-only">Search docs</span><Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search docs" className="input !pl-9" type="search" /></label>
        {q.trim().length >= 2 ? (
          <ul className="mt-4 space-y-1" aria-label="Search results">{res.length === 0 && <li className="text-sm text-gray-400">No matches.</li>}
            {res.map(({ s, pos }) => { const t = (s.text); const at = Math.max(0, t.toLowerCase().indexOf(q.trim().toLowerCase()) - 30);
              return <li key={s.slug + s.id}><Link href={`/docs/${s.slug}#${s.id}`} onClick={() => setQ("")} className="block rounded-lg px-3 py-2 hover:bg-white/5"><span className="block text-sm text-white">{s.title}</span><span className="block text-[11px] text-vocalface-amber">{s.page}</span><span className="block truncate text-xs text-gray-400">{pos >= 0 ? t.slice(at, at + 70) : ""}</span></Link></li>; })}</ul>
        ) : (
          <nav className="mt-6" aria-label="Docs"><p className="font-mono text-xs uppercase tracking-widest text-gray-400">Documentation</p>
            <ul className="mt-3 space-y-1">{[{ slug: "", title: "Overview" }, ...pages].map((p) => { const on = (p.slug || "overview") === active;
              return <li key={p.slug}><Link href={p.slug ? `/docs/${p.slug}` : "/docs"} aria-current={on ? "page" : undefined} className={`block rounded-lg px-3 py-2 text-sm transition ${on ? "bg-white/10 text-white" : "text-gray-300 hover:bg-white/5 hover:text-white"}`}>{p.title}</Link></li>; })}</ul></nav>
        )}
      </aside>
      <article className="min-w-0 max-w-3xl">{children}</article>
      <aside className="hidden lg:sticky lg:top-24 lg:block lg:self-start" aria-label="On this page">
        {toc.length > 0 && <><p className="font-mono text-xs uppercase tracking-widest text-gray-400">On this page</p><ul className="mt-3 space-y-2 border-l border-white/10 pl-4 text-sm">{toc.map((t) => <li key={t.id}><a href={`#${t.id}`} className="text-gray-400 hover:text-white">{t.title}</a></li>)}</ul></>}
      </aside>
    </div>
  );
}
