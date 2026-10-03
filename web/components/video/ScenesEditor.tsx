"use client";
import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { api, errText } from "@/lib/api";
import { Segmented } from "@/components/kit";

export const splitParas = (t: string): string[] => (t.trim() ? t.split(/\n\s*\n/).map((x) => x.trim()).filter(Boolean) : []);
type Preview = { scenes: { index: number; text: string; chars: number; est_seconds: number }[] };

/** Server-side split (same function the renderer uses): scene count, per-scene estimate, and the 12-scene / 5000-char limits as readable errors. */
export function useSceneSplit(script: string, enabled = true) {
  const [info, setInfo] = useState<Preview["scenes"] | null>(null); const [err, setErr] = useState("");
  useEffect(() => {
    if (!enabled || !script.trim()) { setInfo(null); setErr(""); return; }
    const t = setTimeout(() => api<Preview>("/v1/videos/scenes/preview", { body: { script } }).then((r) => { setInfo(r.scenes); setErr(""); }).catch((x) => { setInfo(null); setErr(errText(x)); }), 350);
    return () => clearTimeout(t);
  }, [script, enabled]);
  return { info, err };
}

export default function ScriptEditor({ script, setScript, info, err, placeholder }: { script: string; setScript: (s: string) => void; info: Preview["scenes"] | null; err: string; placeholder: string }) {
  const [view, setView] = useState<"text" | "scenes">("text");
  const scenes = useMemo(() => { const p = splitParas(script); return p.length ? p : [""]; }, [script]);
  const [draft, setDraft] = useState<string[]>(scenes);
  useEffect(() => { if (view === "scenes") setDraft(scenes); }, [view]); // eslint-disable-line react-hooks/exhaustive-deps
  const commit = (d: string[]) => { setDraft(d); setScript(d.map((x) => x.trim()).filter(Boolean).join("\n\n")); };
  const total = info?.reduce((a, s) => a + s.est_seconds, 0) ?? 0;
  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
        <Segmented size="sm" label="Script view" value={view} onChange={setView} options={[{ id: "text", label: "Text" }, { id: "scenes", label: "Scenes" }]} />
        <span className="font-mono text-xs text-gray-500">{script.length} / 5000 chars{info ? ` - ${info.length} scene${info.length === 1 ? "" : "s"}, about ${Math.round(total)}s` : ""}</span>
      </div>
      {view === "text" ? (<>
        <textarea className="input h-40" required aria-label="Script" placeholder={placeholder} maxLength={5000} value={script} onChange={(e) => setScript(e.target.value)} />
        <p className="mt-1.5 text-xs text-gray-500">Tip: leave a blank line between paragraphs to make separate scenes. Switch to Scenes to edit them one by one.</p>
      </>) : (
        <ol className="space-y-2.5" data-testid="scene-list">
          {draft.map((s, i) => (
            <li key={i} className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
              <div className="mb-1.5 flex items-center gap-2 text-xs text-gray-400"><span className="grid h-5 w-5 place-items-center rounded-full bg-mirage-gradient text-[10px] font-semibold text-white">{i + 1}</span>Scene {i + 1}<span className="font-mono text-gray-500">{s.length} chars, about {Math.round(s.length / 15)}s</span>
                <span className="ml-auto flex gap-1">
                  <button type="button" aria-label={`Move scene ${i + 1} up`} disabled={i === 0} className="rounded p-1 hover:bg-white/10 disabled:opacity-30" onClick={() => { const d = [...draft]; [d[i - 1], d[i]] = [d[i], d[i - 1]]; commit(d); }}><ArrowUp size={13} /></button>
                  <button type="button" aria-label={`Move scene ${i + 1} down`} disabled={i === draft.length - 1} className="rounded p-1 hover:bg-white/10 disabled:opacity-30" onClick={() => { const d = [...draft]; [d[i + 1], d[i]] = [d[i], d[i + 1]]; commit(d); }}><ArrowDown size={13} /></button>
                  <button type="button" aria-label={`Delete scene ${i + 1}`} disabled={draft.length === 1} className="rounded p-1 hover:bg-mirage-rose/10 hover:text-mirage-rose disabled:opacity-30" onClick={() => commit(draft.filter((_, j) => j !== i))}><Trash2 size={13} /></button></span></div>
              <textarea className="input h-20 text-sm" aria-label={`Scene ${i + 1} text`} value={s} onChange={(e) => { const d = [...draft]; d[i] = e.target.value; setDraft(d); setScript(d.map((x) => x.trim()).filter(Boolean).join("\n\n")); }} />
            </li>))}
          <li><button type="button" className="btn-ghost !px-3 !py-1.5 text-xs" disabled={draft.length >= 12} onClick={() => setDraft([...draft, ""])}><Plus size={13} />Add scene</button>{draft.length >= 12 && <span className="ml-2 text-xs text-gray-500">12 scenes is the maximum</span>}</li>
        </ol>)}
      {err && <p className="mt-2 text-xs text-mirage-rose" role="alert" data-testid="scene-error">{err}</p>}
    </div>
  );
}
