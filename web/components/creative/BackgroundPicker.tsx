"use client";
import { useCallback, useEffect, useState } from "react";
import { ImageIcon, Trash2 } from "lucide-react";
import { api, apiForm, Asset, errText } from "@/lib/api";
import { Field, Spinner, toast } from "@/components/ui";
import { FileDrop, Segmented, clamp } from "@/components/kit";

export type Bg = { type: "none" } | { type: "color"; color: string } | { type: "gradient"; colors: string[]; angle: number } | { type: "image"; asset_id: string; blur: number } | { type: "blur"; radius: number };
export const NO_BG: Bg = { type: "none" };
/** Server background object (or undefined for "inherit/none"). */
export const bgBody = (b: Bg) => (b.type === "none" ? undefined : b);
export const bgFromServer = (s: { type?: string; color?: string; colors?: string[]; angle?: number; asset_id?: string; blur?: number; radius?: number } | null | undefined): Bg => {
  if (!s || !s.type) return NO_BG;
  if (s.type === "color") return { type: "color", color: s.color || "#101828" };
  if (s.type === "gradient") return { type: "gradient", colors: s.colors?.length ? s.colors : ["#7c5cff", "#ff4d8d"], angle: s.angle ?? 90 };
  if (s.type === "image") return { type: "image", asset_id: s.asset_id || "", blur: s.blur ?? 0 };
  if (s.type === "blur") return { type: "blur", radius: s.radius ?? 25 };
  return NO_BG;
};

const GRADS: [string, string[]][] = [["Dusk", ["#1b1340", "#ff4d8d"]], ["Ocean", ["#0b3a5b", "#22d3ee"]], ["Studio", ["#2a2a35", "#0e0e14"]], ["Peach", ["#ff9e5e", "#ff4d8d"]]];
const SWATCH = ["#101828", "#1f2937", "#0f766e", "#7c5cff", "#ff4d8d", "#f5f1e8"];

/** Live preview: a placeholder presenter (or the replica's face) over the chosen background. For "blur" we blur a busy stand-in room. */
export function BgPreview({ bg, faceSrc, imageSrc, aspect = "16/9" }: { bg: Bg; faceSrc?: string; imageSrc?: string; aspect?: string }) {
  let style: React.CSSProperties = { background: "repeating-linear-gradient(45deg,#2a2d38 0 12px,#20232c 12px 24px)" };
  let inner: React.ReactNode = null;
  if (bg.type === "color") style = { background: bg.color };
  if (bg.type === "gradient") style = { background: `linear-gradient(${bg.angle}deg, ${bg.colors.join(", ")})` };
  if (bg.type === "image") { style = imageSrc ? { background: `center/cover url(${imageSrc})`, filter: bg.blur ? `blur(${bg.blur / 6}px)` : undefined } : { background: "#20232c" }; }
  if (bg.type === "blur") { style = { background: "radial-gradient(circle at 20% 30%,#ff9e5e 0 14%,transparent 15%),radial-gradient(circle at 80% 70%,#22d3ee 0 16%,transparent 17%),repeating-linear-gradient(90deg,#3a3f52 0 18px,#2a2d38 18px 36px)", filter: `blur(${clamp(bg.radius / 5, 1, 12)}px)`, transform: "scale(1.15)" }; }
  if (bg.type === "image" && !imageSrc) inner = <span className="absolute inset-0 grid place-items-center text-gray-500"><ImageIcon size={22} /></span>;
  return (
    <div className="relative w-full overflow-hidden rounded-xl ring-1 ring-white/10" style={{ aspectRatio: aspect }} data-testid="bg-preview" data-bg={bg.type}>
      <div className="absolute inset-0" style={style}>{inner}</div>
      {faceSrc && bg.type === "none"
        // eslint-disable-next-line @next/next/no-img-element
        ? <img src={faceSrc} alt="" className="absolute inset-0 h-full w-full object-cover" />
        : faceSrc
        // eslint-disable-next-line @next/next/no-img-element
        ? <img src={faceSrc} alt="" className="absolute bottom-0 left-1/2 h-[92%] -translate-x-1/2 rounded-t-[40%] object-cover shadow-2xl" style={{ aspectRatio: "3/4" }} />
        : <svg viewBox="0 0 100 100" className="absolute bottom-0 left-1/2 h-[88%] -translate-x-1/2 text-white/35" fill="currentColor" aria-hidden><circle cx="50" cy="36" r="17" /><path d="M14 100c2-26 16-38 36-38s34 12 36 38z" /></svg>}
      <span className="absolute left-2 top-2 rounded-full bg-black/50 px-2 py-0.5 text-[10px] uppercase tracking-wide text-gray-300 backdrop-blur">{bg.type === "none" ? "original" : bg.type}</span>
    </div>
  );
}

export default function BackgroundPicker({ value, onChange, faceSrc, allowNone = true, noneLabel = "Original", hidePreview = false }: { hidePreview?: boolean; value: Bg; onChange: (b: Bg) => void; faceSrc?: string; allowNone?: boolean; noneLabel?: string }) {
  const [assets, setAssets] = useState<Asset[] | null>(null); const [busy, setBusy] = useState(false); const [url, setUrl] = useState(""); const [err, setErr] = useState("");
  const [local, setLocal] = useState<Record<string, string>>({}); // asset_id -> object URL of what we just uploaded (the API serves no asset previews)
  const load = useCallback(() => api<Asset[]>("/v1/creative/assets").then((l) => setAssets(l.filter((a) => a.kind !== "logo"))).catch(() => setAssets([])), []);
  useEffect(() => { if (value.type === "image" && assets === null) load(); }, [value.type, assets, load]);
  useEffect(() => () => { Object.values(local).forEach((u) => URL.revokeObjectURL(u)); }, [local]);

  async function upload(f: File) {
    setBusy(true); setErr("");
    try {
      const fd = new FormData(); fd.append("file", f); fd.append("kind", "background");
      const a = await apiForm<Asset>("/v1/creative/assets", fd);
      setLocal((l) => ({ ...l, [a.id]: URL.createObjectURL(f) })); setAssets((l) => [a, ...(l ?? [])]); onChange({ type: "image", asset_id: a.id, blur: 0 }); toast.success("Background image uploaded.");
    } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  async function fromUrl() {
    if (!url.trim()) return; setBusy(true); setErr("");
    try { const a = await api<Asset>("/v1/creative/assets", { body: { url, kind: "background" } }); setLocal((l) => ({ ...l, [a.id]: url })); setAssets((l) => [a, ...(l ?? [])]); onChange({ type: "image", asset_id: a.id, blur: 0 }); setUrl(""); toast.success("Background image fetched."); }
    catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  async function del(id: string) {
    try { await api(`/v1/creative/assets/${id}`, { method: "DELETE" }); setAssets((l) => (l ?? []).filter((a) => a.id !== id)); if (value.type === "image" && value.asset_id === id) onChange(NO_BG); } catch (x) { toast.error(x); }
  }
  const kinds = [...(allowNone ? [{ id: "none", label: noneLabel }] : []), { id: "color", label: "Colour" }, { id: "gradient", label: "Gradient" }, { id: "image", label: "Image" }, { id: "blur", label: "Blur" }];
  const pick = (t: string) => {
    if (t === "none") onChange(NO_BG); else if (t === "color") onChange({ type: "color", color: "#101828" });
    else if (t === "gradient") onChange({ type: "gradient", colors: GRADS[0][1], angle: 135 }); else if (t === "image") onChange({ type: "image", asset_id: "", blur: 0 }); else onChange({ type: "blur", radius: 25 });
  };
  const img = value.type === "image" ? local[value.asset_id] : undefined;
  return (
    <div className={hidePreview ? "" : "grid gap-4 sm:grid-cols-[1fr_14rem]"}>
      <div className="space-y-3">
        <Segmented size="sm" label="Background type" value={value.type} onChange={pick} options={kinds} />
        {value.type === "color" && (
          <Field label="Colour"><div className="flex flex-wrap items-center gap-2">
            {SWATCH.map((c) => <button key={c} type="button" aria-label={`Colour ${c}`} onClick={() => onChange({ type: "color", color: c })} className={`h-7 w-7 rounded-full ring-2 ${value.color === c ? "ring-white" : "ring-transparent hover:ring-white/40"}`} style={{ background: c }} />)}
            <input type="color" aria-label="Custom colour" value={value.color} onChange={(e) => onChange({ type: "color", color: e.target.value })} className="h-7 w-9 cursor-pointer rounded border border-white/10 bg-transparent" />
            <code className="text-xs text-gray-400">{value.color}</code></div></Field>)}
        {value.type === "gradient" && (<>
          <Field label="Presets"><div className="flex flex-wrap gap-2">{GRADS.map(([n, c]) => <button key={n} type="button" onClick={() => onChange({ type: "gradient", colors: c, angle: value.angle })} className="rounded-lg border border-white/10 px-2 py-1 text-xs text-gray-200 hover:border-white/30"><span className="mr-1.5 inline-block h-3 w-6 rounded align-middle" style={{ background: `linear-gradient(90deg,${c.join(",")})` }} />{n}</button>)}</div></Field>
          <div className="flex flex-wrap items-center gap-3">
            {value.colors.slice(0, 2).map((c, i) => <input key={i} type="color" aria-label={`Gradient colour ${i + 1}`} value={c} onChange={(e) => onChange({ ...value, colors: value.colors.map((x, j) => (j === i ? e.target.value : x)) })} className="h-8 w-10 cursor-pointer rounded border border-white/10 bg-transparent" />)}
            <label className="flex flex-1 items-center gap-2 text-xs text-gray-400">Angle<input type="range" min={0} max={360} value={value.angle} onChange={(e) => onChange({ ...value, angle: Number(e.target.value) })} className="min-w-24 flex-1" aria-label="Gradient angle" />{value.angle}</label></div></>)}
        {value.type === "blur" && <Field label={`Blur strength: ${value.radius}`} hint="Blurs your real background (keeps the room, hides the detail)."><input type="range" min={3} max={99} value={value.radius} aria-label="Blur strength" onChange={(e) => onChange({ type: "blur", radius: Number(e.target.value) })} className="w-full" /></Field>}
        {value.type === "image" && (<div className="space-y-3">
          <FileDrop accept="image/png,image/jpeg,image/webp" label="Upload a background image" hint="PNG, JPEG or WebP, up to 15 MB" busy={busy} onFile={upload} />
          <div className="flex gap-2"><input className="input" type="url" placeholder="or an image URL" aria-label="Background image URL" value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); fromUrl(); } }} /><button type="button" onClick={fromUrl} className="btn" disabled={busy}>{busy ? <Spinner size={14} /> : "Fetch"}</button></div>
          {err && <p className="text-xs text-mirage-rose" role="alert">{err}</p>}
          {assets && assets.length > 0 && <div><p className="label">Your uploads</p><ul className="flex flex-wrap gap-2">{assets.map((a) => (
            <li key={a.id} className={`group relative flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs ${value.asset_id === a.id ? "border-mirage-violet bg-mirage-violet/15" : "border-white/10"}`}>
              <button type="button" onClick={() => onChange({ type: "image", asset_id: a.id, blur: value.blur })} className="max-w-32 truncate text-gray-200">{a.filename || a.id} <span className="text-gray-500">{a.width}x{a.height}</span></button>
              <button type="button" aria-label={`Delete ${a.filename || a.id}`} onClick={() => del(a.id)} className="text-gray-500 hover:text-mirage-rose"><Trash2 size={12} /></button></li>))}</ul></div>}
          {value.asset_id && <Field label={`Softness: ${value.blur}`}><input type="range" min={0} max={40} value={value.blur} aria-label="Image blur" onChange={(e) => onChange({ ...value, blur: Number(e.target.value) })} className="w-full" /></Field>}
        </div>)}
        {value.type === "none" && <p className="text-xs text-gray-500">Keeps the background of the original recording or photo.</p>}
      </div>
      {!hidePreview && <BgPreview bg={value} faceSrc={faceSrc} imageSrc={img} aspect="4/3" />}
    </div>
  );
}
