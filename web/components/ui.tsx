"use client";
import { useEffect, useRef, useState, ReactNode, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import { CheckCircle2, AlertTriangle, Info, X, Copy, Check, Loader2 } from "lucide-react";

/* ---------- toasts (module-level store so any component can call toast.error) ---------- */
type T = { id: number; kind: "error" | "success" | "info"; msg: string };
let toasts: T[] = []; let nid = 1; const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());
function push(kind: T["kind"], msg: string) {
  const id = nid++; toasts = [...toasts, { id, kind, msg }].slice(-4); emit();
  setTimeout(() => { toasts = toasts.filter((t) => t.id !== id); emit(); }, kind === "error" ? 7000 : 4000);
}
export const toast = {
  error: (m: unknown) => push("error", m instanceof Error ? m.message : String(m)),
  success: (m: string) => push("success", m),
  info: (m: string) => push("info", m),
};
export function Toaster() {
  const list = useSyncExternalStore((cb) => { subs.add(cb); return () => { subs.delete(cb); }; }, () => toasts, () => toasts);
  const ico = { error: <AlertTriangle size={16} className="text-mirage-rose" />, success: <CheckCircle2 size={16} className="text-mirage-mint" />, info: <Info size={16} className="text-mirage-cyan" /> };
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[calc(100vw-2rem)] max-w-sm flex-col gap-2">
      <AnimatePresence>
        {list.map((t) => (
          <motion.div key={t.id} layout initial={{ opacity: 0, y: 16, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, x: 40 }}
            className="pointer-events-auto flex items-start gap-3 rounded-xl border border-white/10 bg-ink-3/95 p-3.5 text-sm shadow-2xl backdrop-blur-xl">
            <span className="mt-0.5">{ico[t.kind]}</span><p className="flex-1 break-words text-gray-200">{t.msg}</p>
            <button aria-label="Dismiss" className="text-gray-500 hover:text-white" onClick={() => { toasts = toasts.filter((x) => x.id !== t.id); emit(); }}><X size={14} /></button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

/* ---------- primitives ---------- */
const BADGE: Record<string, string> = {
  ready: "text-mirage-mint bg-mirage-mint/10 ring-mirage-mint/25", active: "text-mirage-mint bg-mirage-mint/10 ring-mirage-mint/25", completed: "text-mirage-mint bg-mirage-mint/10 ring-mirage-mint/25",
  ended: "text-gray-300 bg-white/5 ring-white/10",
  error: "text-mirage-rose bg-mirage-rose/10 ring-mirage-rose/25",
  training: "text-mirage-cyan bg-mirage-cyan/10 ring-mirage-cyan/25", rendering: "text-mirage-cyan bg-mirage-cyan/10 ring-mirage-cyan/25",
  delivered: "text-mirage-mint bg-mirage-mint/10 ring-mirage-mint/25", failed: "text-mirage-rose bg-mirage-rose/10 ring-mirage-rose/25", revoked: "text-gray-400 bg-white/5 ring-white/10", pending: "text-mirage-amber bg-mirage-amber/10 ring-mirage-amber/25",
  queued: "text-mirage-violet bg-mirage-violet/15 ring-mirage-violet/30",
};
export function Badge({ s }: { s: string }) {
  const c = BADGE[s] || "text-mirage-amber bg-mirage-amber/10 ring-mirage-amber/25";
  const pulse = ["training", "rendering", "queued", "active"].includes(s);
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${c}`}>
      <span className={`h-1.5 w-1.5 rounded-full bg-current ${pulse ? "animate-pulse" : ""}`} />{s.replace(/_/g, " ")}
    </span>
  );
}
export const Skeleton = ({ className = "" }: { className?: string }) => (
  <div className={`animate-shimmer rounded-xl bg-[linear-gradient(110deg,rgba(255,255,255,.04)_30%,rgba(255,255,255,.1)_50%,rgba(255,255,255,.04)_70%)] bg-[length:200%_100%] ${className}`} />
);
export const SkeletonCards = ({ n = 3, h = "h-40" }: { n?: number; h?: string }) => (
  <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{Array.from({ length: n }).map((_, i) => <Skeleton key={i} className={h} />)}</div>
);

export function CopyButton({ text, label = "Copy", className = "" }: { text: string; label?: string; className?: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button type="button" className={`inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/5 px-2.5 py-1.5 text-xs text-gray-300 transition hover:bg-white/10 hover:text-white ${className}`}
      onClick={async () => { try { await navigator.clipboard.writeText(text); } catch {} setOk(true); toast.success("Copied to clipboard"); setTimeout(() => setOk(false), 1500); }}>
      {ok ? <Check size={13} className="text-mirage-mint" /> : <Copy size={13} />}{ok ? "Copied" : label}
    </button>
  );
}

const modalStack: object[] = [];
export function Modal({ open, onClose, title, children, side = false, wide = false }: { open: boolean; onClose: () => void; title: string; children: ReactNode; side?: boolean; wide?: boolean }) {
  const [mounted, setMounted] = useState(false); useEffect(() => setMounted(true), []);
  const closeRef = useRef(onClose); closeRef.current = onClose;
  useEffect(() => {
    if (!open) return; const me = {}; modalStack.push(me);
    const f = (e: KeyboardEvent) => { if (e.key === "Escape" && modalStack[modalStack.length - 1] === me) closeRef.current(); };
    window.addEventListener("keydown", f);
    return () => { window.removeEventListener("keydown", f); const i = modalStack.indexOf(me); if (i >= 0) modalStack.splice(i, 1); };
  }, [open]);
  if (!mounted) return null;
  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
          <motion.div role="dialog" aria-label={title}
            initial={side ? { x: "100%" } : { y: 24, opacity: 0 }} animate={side ? { x: 0 } : { y: 0, opacity: 1 }} exit={side ? { x: "100%" } : { y: 24, opacity: 0 }}
            transition={{ type: "spring", damping: 32, stiffness: 320 }}
            className={`relative flex max-h-full flex-col border-white/10 bg-ink-2 shadow-2xl ${side ? `ml-auto h-full w-full ${wide ? "max-w-3xl" : "max-w-xl"} border-l` : "m-auto w-[calc(100%-2rem)] max-w-lg rounded-2xl border"}`}>
            <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
              <h2 className="font-display text-2xl">{title}</h2>
              <button aria-label="Close" onClick={onClose} className="rounded-lg p-1.5 text-gray-400 hover:bg-white/10 hover:text-white"><X size={18} /></button>
            </div>
            <div className="flex-1 overflow-y-auto p-5">{children}</div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>, document.body
  );
}

/* ---------- empty states with inline-SVG illustrations ---------- */
export type IlloKind = "replica" | "persona" | "conversation" | "video" | "ledger" | "knowledge" | "webhook" | "key" | "chart";
export function Illo({ kind }: { kind: IlloKind }) {
  const id = "g" + kind;
  return (
    <svg viewBox="0 0 160 120" className="h-28 w-40" fill="none" aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="160" y2="120" gradientUnits="userSpaceOnUse"><stop stopColor="#ff9e5e" /><stop offset=".5" stopColor="#ff4d8d" /><stop offset="1" stopColor="#7c5cff" /></linearGradient>
        <radialGradient id={id + "b"} cx=".5" cy=".5" r=".5"><stop stopColor="#7c5cff" stopOpacity=".35" /><stop offset="1" stopColor="#7c5cff" stopOpacity="0" /></radialGradient>
      </defs>
      <circle cx="80" cy="60" r="56" fill={`url(#${id}b)`} />
      <g stroke={`url(#${id})`} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        {kind === "replica" && <><circle cx="80" cy="50" r="22" /><path d="M42 104c4-22 20-30 38-30s34 8 38 30" /><path d="M24 30v-8h8M136 30v-8h-8M24 90v8h8M136 90v8h-8" opacity=".6" /></>}
        {kind === "persona" && <><circle cx="80" cy="44" r="16" /><path d="M52 98c3-18 14-26 28-26s25 8 28 26" /><path d="M112 28l3 7 7 3-7 3-3 7-3-7-7-3 7-3z" /><path d="M42 40l2 4 4 2-4 2-2 4-2-4-4-2 4-2z" opacity=".6" /></>}
        {kind === "conversation" && <><rect x="28" y="28" width="72" height="44" rx="14" /><path d="M48 72v14l16-14" /><rect x="76" y="52" width="58" height="38" rx="12" opacity=".7" /><path d="M92 68h26M92 76h16" opacity=".7" /><path d="M46 46h36M46 56h22" /></>}
        {kind === "video" && <><rect x="26" y="30" width="84" height="60" rx="12" /><path d="M110 52l26-14v44l-26-14z" /><path d="M64 48l16 12-16 12z" /></>}
        {kind === "ledger" && <><rect x="40" y="24" width="80" height="76" rx="10" /><path d="M56 48h48M56 62h48M56 76h28" /></>}
        {kind === "webhook" && <><circle cx="54" cy="84" r="12" /><circle cx="106" cy="84" r="12" /><circle cx="80" cy="36" r="12" /><path d="M72 46l-16 28M88 46l16 28M66 84h28" /></>}
        {kind === "key" && <><circle cx="58" cy="60" r="20" /><path d="M76 68l44 30M104 86l8-8M114 94l8-8" /><circle cx="58" cy="60" r="6" opacity=".6" /></>}
        {kind === "chart" && <><path d="M32 24v76h100" /><path d="M48 84V64M70 84V44M92 84V58M114 84V34" /></>}
        {kind === "knowledge" && <><path d="M44 28h50l22 22v52H44z" /><path d="M94 28v22h22M58 66h44M58 80h30" /></>}
      </g>
    </svg>
  );
}
export function Empty({ kind, title, hint, action }: { kind: IlloKind; title: string; hint: string; action?: ReactNode }) {
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col items-center rounded-2xl border border-dashed border-white/15 bg-white/[0.02] px-6 py-12 text-center">
      <Illo kind={kind} />
      <h3 className="mt-2 font-display text-2xl">{title}</h3>
      <p className="mt-1 max-w-sm text-sm text-gray-400">{hint}</p>
      {action && <div className="mt-5">{action}</div>}
    </motion.div>
  );
}

/* ---------- page header used by every dashboard page (the shell itself lives in app/dashboard/layout.tsx) ---------- */
export function Shell({ title, subtitle, action, children }: { title: string; subtitle?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}>
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-4xl leading-tight md:text-5xl">{title}</h1>
          {subtitle && <p className="mt-1 max-w-xl text-sm text-gray-400">{subtitle}</p>}
        </div>
        {action && <div className="flex items-center gap-2">{action}</div>}
      </div>
      {children}
    </motion.div>
  );
}

/* credits refresh signal */
export const refreshCredits = () => { try { window.dispatchEvent(new Event("mirage:credits")); } catch {} };


/* ---------- shared form / layout helpers ---------- */
export function Spinner({ size = 15 }: { size?: number }) { return <Loader2 size={size} className="animate-spin" />; }

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string; badge?: number | string }[]; value: T; onChange: (t: T) => void }) {
  return (
    <div role="tablist" className="no-scrollbar -mx-1 mb-5 flex gap-1 overflow-x-auto border-b border-white/10 px-1">
      {tabs.map((t) => (
        <button key={t.id} role="tab" type="button" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={`relative flex shrink-0 items-center gap-1.5 px-3 pb-2.5 pt-1 text-sm transition ${value === t.id ? "text-white" : "text-gray-500 hover:text-gray-200"}`}>
          {t.label}{t.badge !== undefined && t.badge !== 0 && <span className="rounded-full bg-white/10 px-1.5 text-[10px] text-gray-300">{t.badge}</span>}
          {value === t.id && <motion.span layoutId="tabline" className="absolute inset-x-2 -bottom-px h-0.5 rounded bg-mirage-gradient" />}
        </button>
      ))}
    </div>
  );
}

export function Section({ title, hint, icon, action, children }: { title: string; hint?: string; icon?: ReactNode; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="mb-7">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div><h3 className="flex items-center gap-2 font-medium">{icon}{title}</h3>{hint && <p className="mt-0.5 text-xs leading-relaxed text-gray-400">{hint}</p>}</div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <button type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}
        className={`relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition ${checked ? "bg-mirage-gradient" : "bg-white/15"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${checked ? "left-[18px]" : "left-0.5"}`} />
      </button>
      <span className="text-sm text-gray-200">{label}{hint && <span className="block text-xs text-gray-500">{hint}</span>}</span>
    </label>
  );
}

/** Confirmation dialog. With `typed`, the confirm button stays disabled until that exact phrase is typed. */
export function ConfirmDialog({ open, title, body, confirmLabel = "Confirm", typed, danger = true, onConfirm, onClose }: {
  open: boolean; title: string; body: ReactNode; confirmLabel?: string; typed?: string; danger?: boolean; onConfirm: () => Promise<void> | void; onClose: () => void;
}) {
  const [txt, setTxt] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { if (!open) setTxt(""); }, [open]);
  const ok = !typed || txt === typed;
  return (
    <Modal open={open} onClose={onClose} title={title}>
      <div className="space-y-4">
        <div className="text-sm leading-relaxed text-gray-300">{body}</div>
        {typed && <div><label className="label">Type <span className="font-mono normal-case text-white">{typed}</span> to confirm</label><input className="input font-mono" autoFocus value={txt} onChange={(e) => setTxt(e.target.value)} aria-label="Confirmation phrase" /></div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" disabled={!ok || busy} onClick={async () => { setBusy(true); try { await onConfirm(); } finally { setBusy(false); } }}
            className={`inline-flex items-center justify-center gap-2 rounded-full px-5 py-2.5 text-sm font-semibold transition disabled:opacity-40 ${danger ? "bg-mirage-rose text-white hover:brightness-110" : "bg-white text-ink"}`}>
            {busy && <Spinner />}{confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}

/** A secret that is shown exactly once (API key, webhook signing secret). */
export function SecretOnce({ title, secret, note, onDone }: { title: string; secret: string; note: string; onDone: () => void }) {
  return (
    <div className="rounded-2xl border border-mirage-amber/30 bg-mirage-amber/[0.06] p-4" role="alert">
      <p className="flex items-center gap-2 text-sm font-medium text-mirage-amber"><AlertTriangle size={15} />{title}</p>
      <p className="mt-1 text-xs text-gray-400">{note}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <code data-testid="secret-once" className="min-w-0 flex-1 basis-full break-all rounded-lg bg-black/40 px-3 py-2 font-mono text-xs text-gray-100 sm:basis-0">{secret}</code>
        <CopyButton text={secret} />
        <button type="button" className="btn-ghost !px-3 !py-1.5 text-xs" onClick={onDone}>I saved it</button>
      </div>
    </div>
  );
}

export function Stat({ label, value, sub, accent }: { label: string; value: ReactNode; sub?: string; accent?: string }) {
  return (
    <div className="card !p-4">
      <p className="label !mb-2">{label}</p>
      <p className={`font-display text-4xl leading-none ${accent ?? ""}`}>{value}</p>
      {sub && <p className="mt-2 text-xs text-gray-500">{sub}</p>}
    </div>
  );
}

export const Field = ({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) => (
  <div><label className="label">{label}</label>{children}{hint && <p className="mt-1 text-xs text-gray-500">{hint}</p>}</div>
);
