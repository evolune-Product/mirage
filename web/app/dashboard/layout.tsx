"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { LayoutDashboard, ScanFace, UserRound, MessagesSquare, Clapperboard, CreditCard, KeyRound, LogOut, Menu, X, BarChart3, Webhook, Settings, Users, Inbox } from "lucide-react";
import Logo from "@/components/site/Logo";
import { Toaster } from "@/components/ui";
import { api, clearKey, getKey, getWorkspace, setWorkspace, Workspace } from "@/lib/api";

const groups = [
  { title: "Studio", items: [
    { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
    { href: "/dashboard/replicas", label: "Replicas", icon: ScanFace },
    { href: "/dashboard/personas", label: "Personas", icon: UserRound },
    { href: "/dashboard/conversations", label: "Conversations", icon: MessagesSquare },
    { href: "/dashboard/videos", label: "Videos", icon: Clapperboard },
  ] },
  { title: "Insights", items: [{ href: "/dashboard/analytics", label: "Analytics", icon: BarChart3 }, { href: "/dashboard/leads", label: "Leads", icon: Inbox }] },
  { title: "Developers", items: [
    { href: "/dashboard/webhooks", label: "Webhooks", icon: Webhook },
    { href: "/dashboard/keys", label: "API keys & docs", icon: KeyRound },
  ] },
  { title: "Account", items: [
    { href: "/dashboard/billing", label: "Billing", icon: CreditCard },
    { href: "/dashboard/team", label: "Team", icon: Users },
    { href: "/dashboard/settings", label: "Settings", icon: Settings },
  ] },
];
const nav = groups.flatMap((g) => g.items);
type Status = { plan: { name: string; included_minutes: number }; credits_seconds: number };

function WorkspaceChip({ onNav }: { onNav: () => void }) {
  const [name, setName] = useState<string | null>(null); const [id, setId] = useState("");
  useEffect(() => {
    const f = () => { const w = getWorkspace(); setId(w); if (!w) { setName(null); return; } api<Workspace[]>("/v1/workspaces").then((l) => { const m = l.find((x) => x.id === w); if (m) setName(`${m.name} (${m.role})`); else { setWorkspace(""); setId(""); } }).catch(() => {}); };
    f(); window.addEventListener("mirage:workspace", f); return () => window.removeEventListener("mirage:workspace", f);
  }, []);
  return (
    <Link href="/dashboard/team" onClick={onNav} data-testid="ws-chip" className={`mx-3 mb-3 flex items-center gap-2 rounded-lg border px-3 py-2 text-xs transition ${id ? "border-mirage-mint/30 bg-mirage-mint/[0.06] text-mirage-mint" : "border-white/10 text-gray-400 hover:bg-white/5 hover:text-white"}`}>
      <Users size={13} /><span className="min-w-0 flex-1 truncate">{id ? name ?? "Workspace" : "Personal account"}</span><span className="text-gray-500">switch</span>
    </Link>
  );
}

function Sidebar({ path, credits, onNav, signOut }: { path: string; credits: Status | null; onNav: () => void; signOut: () => void }) {
  const secs = credits?.credits_seconds ?? 0;
  const total = Math.max(secs, (credits?.plan.included_minutes ?? 0) * 60, 600);
  const pct = Math.min(100, (secs / total) * 100);
  const low = credits !== null && secs < 120;
  return (
    <div className="flex h-full flex-col">
      <Link href="/" className="flex items-center px-5 py-5"><Logo /></Link>
      <WorkspaceChip onNav={onNav} />
      <nav className="flex-1 space-y-4 overflow-y-auto px-3 pb-2">
        {groups.map((g) => (
          <div key={g.title}>
            <p className="mb-1 px-3 font-mono text-[10px] uppercase tracking-[0.18em] text-gray-600">{g.title}</p>
            <div className="space-y-0.5">
              {g.items.map(({ href, label, icon: I }) => {
                const on = path === href;
                return (
                  <Link key={href} href={href} onClick={onNav} className={`group relative flex items-center gap-3 rounded-lg px-3 py-1.5 text-sm transition ${on ? "text-white" : "text-gray-400 hover:bg-white/5 hover:text-white"}`}>
                    {on && <motion.span layoutId="navbg" className="absolute inset-0 rounded-lg bg-white/[0.08] ring-1 ring-white/10" />}
                    {on && <span className="absolute -left-3 top-2 bottom-2 w-0.5 rounded bg-mirage-gradient" />}
                    <I size={16} className={`relative ${on ? "text-mirage-rose" : ""}`} /><span className="relative">{label}</span>
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="m-3 space-y-3 rounded-xl border border-white/10 bg-ink-3/70 p-3.5">
        <div className="flex items-center justify-between">
          <span className="text-xs text-gray-400">Credits</span>
          <span className="rounded-full bg-mirage-gradient px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">{credits?.plan.name ?? "..."}</span>
        </div>
        <div>
          <p className="font-mono text-lg leading-none">{credits ? `${Math.floor(secs / 60)}m ${secs % 60}s` : "--"}</p>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10"><motion.div className={`h-full rounded-full ${low ? "bg-mirage-rose" : "bg-mirage-gradient"}`} initial={{ width: 0 }} animate={{ width: pct + "%" }} transition={{ duration: 0.8 }} /></div>
        </div>
        <Link href="/dashboard/billing" onClick={onNav} className="block text-xs text-gray-400 hover:text-white">{low ? "Running low - top up" : "Manage billing"} &rarr;</Link>
      </div>
      <button onClick={signOut} className="mx-3 mb-3 flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-gray-500 transition hover:bg-white/5 hover:text-white"><LogOut size={16} />Sign out</button>
    </div>
  );
}

export default function DashLayout({ children }: { children: ReactNode }) {
  const path = usePathname(); const router = useRouter();
  const [ok, setOk] = useState(false); const [open, setOpen] = useState(false); const [credits, setCredits] = useState<Status | null>(null);
  useEffect(() => { if (!getKey()) router.replace("/signup"); else setOk(true); }, [router]);
  useEffect(() => {
    if (!ok) return;
    const load = () => api<Status>("/v1/billing/status").then(setCredits).catch(() => {});
    load(); const t = setInterval(load, 30000); window.addEventListener("mirage:credits", load);
    return () => { clearInterval(t); window.removeEventListener("mirage:credits", load); };
  }, [ok]);
  useEffect(() => setOpen(false), [path]);
  // Render the sidebar once only: the Logo's SVG gradient id is shared, so a hidden duplicate would break it.
  const [desktop, setDesktop] = useState(true);
  useEffect(() => { const mq = window.matchMedia("(min-width: 768px)"); const f = () => setDesktop(mq.matches); f(); mq.addEventListener("change", f); return () => mq.removeEventListener("change", f); }, []);
  const signOut = () => { clearKey(); router.push("/"); };
  const current = nav.find((n) => n.href === path)?.label ?? "Dashboard";
  if (!ok) return <div className="grid min-h-screen place-items-center bg-ink"><div className="h-8 w-8 animate-spin rounded-full border-2 border-white/10 border-t-mirage-rose" /></div>;
  return (
    <div className="min-h-screen bg-ink bg-[radial-gradient(60rem_30rem_at_80%_-10%,rgba(124,92,255,.10),transparent),radial-gradient(40rem_25rem_at_0%_0%,rgba(255,77,141,.07),transparent)]">
      {desktop && <aside className="fixed inset-y-0 left-0 z-30 w-64 border-r border-white/[0.07] bg-ink/80 backdrop-blur"><Sidebar path={path} credits={credits} onNav={() => {}} signOut={signOut} /></aside>}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-white/[0.07] bg-ink/85 px-4 py-3 backdrop-blur md:hidden">
        <button aria-label="Open menu" onClick={() => setOpen(true)} className="rounded-lg p-1.5 text-gray-300 hover:bg-white/10"><Menu size={20} /></button>
        <span className="text-sm font-medium">{current}</span>
        <Link href="/dashboard/billing" className="rounded-full bg-white/10 px-2.5 py-1 font-mono text-xs">{credits ? `${Math.floor(credits.credits_seconds / 60)}m` : "--"}</Link>
      </header>
      <AnimatePresence>
        {open && !desktop && (
          <motion.div className="fixed inset-0 z-50 md:hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
            <motion.div initial={{ x: -280 }} animate={{ x: 0 }} exit={{ x: -280 }} transition={{ type: "spring", damping: 30, stiffness: 300 }} className="relative h-full w-72 bg-ink-2 shadow-2xl">
              <button aria-label="Close menu" onClick={() => setOpen(false)} className="absolute right-3 top-4 rounded-lg p-1.5 text-gray-400 hover:bg-white/10"><X size={18} /></button>
              <Sidebar path={path} credits={credits} onNav={() => setOpen(false)} signOut={signOut} />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
      <main className="px-4 py-8 md:ml-64 md:px-10 md:py-10"><div className="mx-auto max-w-6xl">{children}</div></main>
      <Toaster />
    </div>
  );
}
