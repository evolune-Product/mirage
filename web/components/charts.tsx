"use client";
import { useState } from "react";

export type Point = { label: string; value: number };
const nice = (max: number) => { if (max <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(max))); const n = max / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; };

/** Single-series column chart: thin rounded-top bars on a baseline, recessive grid, hover tooltip, sparse x labels. */
export function BarChart({ data, color, unit, height = 160, fmt = (v: number) => String(+v.toFixed(2)), title }: { data: Point[]; color: string; unit: string; height?: number; fmt?: (v: number) => string; title: string }) {
  const [hot, setHot] = useState<number | null>(null);
  const top = nice(Math.max(...data.map((d) => d.value), 0));
  const ticks = [top, top / 2, 0];
  const every = Math.max(1, Math.ceil(data.length / 6));
  const total = data.reduce((a, d) => a + d.value, 0);
  return (
    <div role="img" aria-label={`${title}: ${fmt(total)} ${unit} in total over ${data.length} days`}>
      <div className="flex gap-2" style={{ height }}>
        <div className="flex flex-col justify-between pb-0 text-right font-mono text-[10px] text-gray-500" aria-hidden>{ticks.map((t, i) => <span key={i} className="leading-none">{fmt(t)}</span>)}</div>
        <div className="relative flex-1">
          <div className="pointer-events-none absolute inset-0 flex flex-col justify-between" aria-hidden>{ticks.map((_, i) => <div key={i} className={`h-px w-full ${i === 2 ? "bg-white/25" : "bg-white/[0.07]"}`} />)}</div>
          <div className="absolute inset-0 flex items-end gap-px" onMouseLeave={() => setHot(null)}>
            {data.map((d, i) => (
              <div key={d.label} className="group relative flex h-full flex-1 items-end" onMouseEnter={() => setHot(i)} onFocus={() => setHot(i)} tabIndex={0} aria-label={`${d.label}: ${fmt(d.value)} ${unit}`}>
                <div className="w-full rounded-t-[3px] transition-opacity" style={{ height: `${(d.value / top) * 100}%`, minHeight: d.value > 0 ? 2 : 0, background: color, opacity: hot === null || hot === i ? 1 : 0.45 }} />
                {hot === i && (
                  <div className="pointer-events-none absolute bottom-full z-10 mb-1.5 whitespace-nowrap rounded-lg border border-white/10 bg-ink-3 px-2.5 py-1.5 text-xs shadow-xl"
                    style={{ left: i > data.length * 0.7 ? "auto" : "50%", right: i > data.length * 0.7 ? 0 : "auto", transform: i > data.length * 0.7 ? "none" : "translateX(-50%)" }}>
                    <span className="text-gray-400">{d.label}</span> <span className="font-mono text-white">{fmt(d.value)} {unit}</span>
                  </div>)}
              </div>))}
          </div>
        </div>
      </div>
      <div className="ml-[calc(2rem+0.5rem)] mt-1.5 flex gap-px font-mono text-[10px] text-gray-500" aria-hidden>
        {data.map((d, i) => <span key={d.label} className="flex-1 overflow-visible whitespace-nowrap">{i % every === 0 ? d.label.slice(5) : ""}</span>)}
      </div>
    </div>
  );
}

/** Horizontal ranked bars, one hue, value as text. */
export function HBars({ rows, color, fmt = String }: { rows: { label: string; value: number; sub?: string }[]; color: string; fmt?: (v: number) => string }) {
  const max = Math.max(...rows.map((r) => r.value), 1);
  return (
    <ul className="space-y-3">{rows.map((r) => (
      <li key={r.label}>
        <div className="mb-1 flex items-baseline justify-between gap-3 text-sm"><span className="truncate">{r.label}</span><span className="shrink-0 font-mono text-xs text-gray-300">{fmt(r.value)}{r.sub && <span className="ml-1.5 text-gray-500">{r.sub}</span>}</span></div>
        <div className="h-2 overflow-hidden rounded-full bg-white/[0.06]"><div className="h-full rounded-full" style={{ width: `${(r.value / max) * 100}%`, background: color }} /></div>
      </li>))}</ul>
  );
}

/** Stacked single-row status bar with a 2px gap between segments and a legend. */
export function StatusBar({ parts }: { parts: { label: string; value: number; color: string }[] }) {
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  return (
    <div>
      <div className="flex h-3 gap-0.5 overflow-hidden rounded-full">{parts.filter((p) => p.value > 0).map((p) => <div key={p.label} title={`${p.label}: ${p.value}`} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} />)}</div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-300">{parts.map((p) => <li key={p.label} className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full" style={{ background: p.color }} />{p.label} <span className="font-mono text-gray-500">{p.value}</span></li>)}</ul>
    </div>
  );
}
