"use client";
/** Small shared controls added for dashboard-2 (kept out of ui.tsx so the older primitives stay untouched). */
import { ReactNode, useRef, useState } from "react";
import { Upload } from "lucide-react";

export function Progress({ pct, label, tone = "grad", className = "" }: { pct: number; label?: ReactNode; tone?: "grad" | "mint" | "rose"; className?: string }) {
  const p = Math.max(0, Math.min(100, pct));
  const c = tone === "mint" ? "bg-mirage-mint" : tone === "rose" ? "bg-mirage-rose" : "bg-mirage-gradient";
  return (
    <div className={className}>
      <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(p)} className="h-1.5 overflow-hidden rounded-full bg-white/10">
        <div className={`h-full rounded-full transition-all duration-700 ${c}`} style={{ width: p + "%" }} />
      </div>
      {label && <p className="mt-1 text-[11px] text-gray-500">{label}</p>}
    </div>
  );
}

/** Segmented single-choice control (radio group semantics). */
export function Segmented<T extends string | number>({ value, onChange, options, label, size = "md" }: {
  value: T; onChange: (v: T) => void; options: { id: T; label: ReactNode; hint?: string }[]; label: string; size?: "sm" | "md";
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-1.5">
      {options.map((o) => {
        const on = o.id === value;
        return (
          <button key={String(o.id)} type="button" role="radio" aria-checked={on} title={o.hint} onClick={() => onChange(o.id)}
            className={`rounded-full border transition ${size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-1.5 text-sm"} ${on ? "border-mirage-violet/60 bg-mirage-violet/20 text-white" : "border-white/10 text-gray-400 hover:border-white/25 hover:text-white"}`}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** File chooser with a drop zone. Calls onFile with the chosen File. */
export function FileDrop({ accept, onFile, label, hint, busy, name }: { accept: string; onFile: (f: File) => void; label: string; hint?: string; busy?: boolean; name?: string }) {
  const ref = useRef<HTMLInputElement>(null); const [over, setOver] = useState(false);
  return (
    <div>
      <button type="button" disabled={busy} aria-label={label}
        onClick={() => ref.current?.click()} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files?.[0]; if (f) onFile(f); }}
        className={`flex w-full flex-col items-center gap-1 rounded-xl border border-dashed px-4 py-5 text-sm transition ${over ? "border-mirage-violet bg-mirage-violet/10" : "border-white/20 bg-white/[0.02] hover:bg-white/[0.05]"} disabled:opacity-50`}>
        <Upload size={18} className="text-gray-400" /><span className="text-gray-200">{name || label}</span>{hint && <span className="text-xs text-gray-500">{hint}</span>}
      </button>
      <input ref={ref} type="file" accept={accept} className="hidden" data-testid="file-input" aria-hidden tabIndex={-1}
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
    </div>
  );
}

/** Plain-language callout. tone: info (cyan) | warn (amber) | bad (rose) | ok (mint). */
export function Callout({ tone = "info", title, children }: { tone?: "info" | "warn" | "bad" | "ok"; title?: ReactNode; children: ReactNode }) {
  const t = { info: "border-mirage-cyan/25 bg-mirage-cyan/[0.06] text-mirage-cyan", warn: "border-mirage-amber/30 bg-mirage-amber/[0.06] text-mirage-amber", bad: "border-mirage-rose/30 bg-mirage-rose/[0.07] text-mirage-rose", ok: "border-mirage-mint/25 bg-mirage-mint/[0.06] text-mirage-mint" }[tone];
  return (
    <div className={`rounded-xl border p-3.5 text-xs leading-relaxed ${t.split(" ").slice(0, 2).join(" ")}`} role={tone === "bad" ? "alert" : undefined}>
      {title && <p className={`mb-1 font-medium ${t.split(" ")[2]}`}>{title}</p>}
      <div className="text-gray-300">{children}</div>
    </div>
  );
}

export const clamp = (n: number, a: number, b: number) => Math.min(b, Math.max(a, n));
