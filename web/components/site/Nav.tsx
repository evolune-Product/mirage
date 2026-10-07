"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, Menu, X } from "lucide-react";
import Logo from "./Logo";

const links = [["Product", "/#product"], ["Use cases", "/use-cases"], ["Docs", "/docs"], ["Security", "/security"], ["Pricing", "/pricing"]];

export default function Nav() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => { const f = () => setScrolled(window.scrollY > 20); f(); window.addEventListener("scroll", f, { passive: true }); return () => window.removeEventListener("scroll", f); }, []);
  return (
    <>
      <div className="relative z-[60] bg-vocalface-gradient px-4 py-2 text-center text-xs font-medium text-white sm:text-[13px]">
        Open-core and self-hostable. Live real-time face rendering on GPU workers is coming soon. <Link href="/#roadmap" className="underline underline-offset-2">See what ships today <ArrowRight size={12} className="inline" /></Link>
      </div>
      <header className={`sticky top-0 z-50 transition ${scrolled || open ? "border-b border-white/10 bg-ink/90 backdrop-blur-xl" : "border-b border-transparent"}`}>
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5">
          <Link href="/" aria-label="VocalFace home"><Logo /></Link>
          <nav className="hidden items-center gap-8 text-sm text-gray-300 md:flex" aria-label="Main">{links.map(([l, h]) => <Link key={l} href={h} className="transition hover:text-white">{l}</Link>)}</nav>
          <div className="hidden items-center gap-3 md:flex"><Link href="/dashboard" className="text-sm text-gray-300 hover:text-white">Dashboard</Link><Link href="/signup" className="btn-grad">Start free</Link></div>
          <button className="grid h-10 w-10 place-items-center rounded-full border border-white/15 md:hidden" aria-label="Menu" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? <X size={18} /> : <Menu size={18} />}</button>
        </div>
        {open && (
          <div className="border-t border-white/10 px-5 pb-6 pt-3 md:hidden">
            {links.map(([l, h]) => <Link key={l} href={h} onClick={() => setOpen(false)} className="block border-b border-white/5 py-3 text-gray-200">{l}</Link>)}
            <div className="mt-4 flex gap-3"><Link href="/signup" className="btn-grad flex-1">Start free</Link><Link href="/dashboard" className="btn-ghost flex-1">Dashboard</Link></div>
          </div>
        )}
      </header>
    </>
  );
}
