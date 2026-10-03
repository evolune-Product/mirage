"use client";
import { useCallback, useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import { api, apiForm, Asset, errText } from "@/lib/api";
import { Field, Spinner, Toggle, toast } from "@/components/ui";
import { FileDrop, Segmented } from "@/components/kit";
import BackgroundPicker, { BgPreview, Bg, NO_BG, bgBody } from "@/components/creative/BackgroundPicker";

export type Logo = { asset_id: string; position: string; scale: number; opacity: number };
export type Opts = {
  format: "16:9" | "9:16" | "1:1"; resolution: 480 | 720 | 1080; captions: "off" | "classic" | "bold" | "minimal" | "karaoke"; accent: string;
  bg: Bg; logo: Logo | null; transition: "cut" | "fade" | "dip" | "slide"; transition_s: number; scenes: "paragraphs" | "single"; thumbnail: boolean; sharpen: boolean;
  voice: string; // "default" | "clone" | preset id
};
export const DEFAULT_OPTS: Opts = { format: "16:9", resolution: 720, captions: "off", accent: "#ffd23f", bg: NO_BG, logo: null, transition: "fade", transition_s: 0.4, scenes: "paragraphs", thumbnail: true, sharpen: false, voice: "default" };
/** Everything except voice is a "creative" option: changing any of them switches the job to the studio renderer. */
export const creativeDirty = (o: Opts) => JSON.stringify({ ...o, voice: "", scenes: "", transition: "", transition_s: 0, thumbnail: true }) !== JSON.stringify({ ...DEFAULT_OPTS, voice: "", scenes: "", transition: "", transition_s: 0, thumbnail: true });
export const optsBody = (o: Opts) => ({
  format: o.format, resolution: o.resolution, background: bgBody(o.bg), captions: o.captions === "off" ? undefined : { style: o.captions, accent: o.accent },
  logo: o.logo ?? undefined, transition: o.transition, transition_s: o.transition_s, scenes: o.scenes, thumbnail: o.thumbnail, restore: o.sharpen ? "sr" : "none",
});

const ASPECT: Record<string, string> = { "16:9": "16/9", "9:16": "9/16", "1:1": "1/1" };
const POS = ["top-left", "top-right", "bottom-left", "bottom-right"] as const;

function CaptionSample({ style, accent }: { style: string; accent: string }) {
  if (style === "off") return null;
  const words = ["Welcome", "to", "Mirage"];
  const base = style === "bold" ? "text-xl font-black uppercase tracking-tight [text-shadow:0_2px_0_#000,0_0_8px_#000]" : style === "minimal" ? "text-[11px] font-light lowercase" : style === "karaoke" ? "text-base font-extrabold [text-shadow:0_2px_0_#000]" : "rounded bg-black/65 px-2 py-0.5 text-sm font-medium";
  return <div className={`absolute inset-x-0 bottom-[10%] flex justify-center gap-1 text-white ${base}`}>{words.map((w, i) => <span key={w} style={style === "karaoke" && i === 1 ? { color: accent } : style === "bold" ? { color: accent } : undefined}>{w}</span>)}</div>;
}

export default function CreativePanel({ o, set, faceSrc, voices, sceneCount }: { o: Opts; set: (o: Opts) => void; faceSrc?: string; voices: { value: string; label: string }[]; sceneCount: number }) {
  const [assets, setAssets] = useState<Asset[]>([]); const [busy, setBusy] = useState(false); const [url, setUrl] = useState(""); const [err, setErr] = useState(""); const [local, setLocal] = useState<Record<string, string>>({});
  const load = useCallback(() => api<Asset[]>("/v1/creative/assets").then((l) => setAssets(l.filter((a) => a.kind === "logo"))).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  const patch = (p: Partial<Opts>) => set({ ...o, ...p });
  async function upload(f: File) {
    setBusy(true); setErr("");
    try { const fd = new FormData(); fd.append("file", f); fd.append("kind", "logo"); const a = await apiForm<Asset>("/v1/creative/assets", fd); setLocal((l) => ({ ...l, [a.id]: URL.createObjectURL(f) })); setAssets((l) => [a, ...l]); patch({ logo: { asset_id: a.id, position: o.logo?.position ?? "top-right", scale: o.logo?.scale ?? 0.14, opacity: o.logo?.opacity ?? 0.9 } }); toast.success("Logo uploaded."); }
    catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  async function fromUrl() {
    if (!url.trim()) return; setBusy(true); setErr("");
    try { const a = await api<Asset>("/v1/creative/assets", { body: { url, kind: "logo" } }); setLocal((l) => ({ ...l, [a.id]: url })); setAssets((l) => [a, ...l]); patch({ logo: { asset_id: a.id, position: "top-right", scale: 0.14, opacity: 0.9 } }); setUrl(""); }
    catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  async function del(id: string) { try { await api(`/v1/creative/assets/${id}`, { method: "DELETE" }); setAssets((l) => l.filter((a) => a.id !== id)); if (o.logo?.asset_id === id) patch({ logo: null }); } catch (x) { toast.error(x); } }
  const logoSrc = o.logo ? local[o.logo.asset_id] : undefined;
  const tall = o.format === "9:16";
  const sec = "rounded-xl border border-white/10 bg-white/[0.02] p-4";
  return (
    <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_15rem]" data-testid="creative-panel">
      <div className="space-y-4">
        <div className={sec}>
          <p className="label">Format</p>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
            <Segmented size="sm" label="Aspect ratio" value={o.format} onChange={(v) => patch({ format: v })} options={[{ id: "16:9", label: "16:9 landscape" }, { id: "9:16", label: "9:16 vertical" }, { id: "1:1", label: "1:1 square" }]} />
            <Segmented size="sm" label="Resolution" value={o.resolution} onChange={(v) => patch({ resolution: v })} options={[{ id: 480, label: "480p" }, { id: 720, label: "720p" }, { id: 1080, label: "1080p" }]} />
          </div>
          {o.format !== "16:9" && <p className="mt-2 text-xs text-gray-500">Vertical and square crops follow the face. Footage recorded at 720p webcam quality looks soft in 9:16; use 1:1 or 16:9 for a sharper result.</p>}
        </div>
        <div className={sec}>
          <p className="label">Captions</p>
          <Segmented size="sm" label="Caption style" value={o.captions} onChange={(v) => patch({ captions: v })} options={[{ id: "off", label: "None" }, { id: "classic", label: "Classic" }, { id: "bold", label: "Bold" }, { id: "minimal", label: "Minimal" }, { id: "karaoke", label: "Karaoke" }]} />
          {o.captions !== "off" && <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-gray-400"><label className="flex items-center gap-2">Accent colour<input type="color" aria-label="Caption accent colour" value={o.accent} onChange={(e) => patch({ accent: e.target.value })} className="h-7 w-9 cursor-pointer rounded border border-white/10 bg-transparent" /></label><span>Timed from the generated speech. An .srt file is saved too.</span></div>}
        </div>
        <div className={sec}><p className="label">Background</p><BackgroundPicker value={o.bg} onChange={(b) => patch({ bg: b })} faceSrc={faceSrc} noneLabel="Replica default" hidePreview /></div>
        <div className={sec}>
          <p className="label">Logo watermark</p>
          {o.logo ? (<div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-xs"><span className="rounded-lg bg-white/5 px-2 py-1 text-gray-200">{assets.find((a) => a.id === o.logo!.asset_id)?.filename || o.logo.asset_id}</span><button type="button" className="text-mirage-rose hover:underline" onClick={() => patch({ logo: null })}>Remove logo</button></div>
            <Segmented size="sm" label="Logo position" value={o.logo.position} onChange={(v) => patch({ logo: { ...o.logo!, position: v } })} options={POS.map((p) => ({ id: p, label: p.replace("-", " ") }))} />
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={`Size: ${Math.round(o.logo.scale * 100)}%`}><input type="range" min={4} max={50} value={Math.round(o.logo.scale * 100)} aria-label="Logo size" onChange={(e) => patch({ logo: { ...o.logo!, scale: Number(e.target.value) / 100 } })} className="w-full" /></Field>
              <Field label={`Opacity: ${Math.round(o.logo.opacity * 100)}%`}><input type="range" min={10} max={100} value={Math.round(o.logo.opacity * 100)} aria-label="Logo opacity" onChange={(e) => patch({ logo: { ...o.logo!, opacity: Number(e.target.value) / 100 } })} className="w-full" /></Field></div></div>
          ) : (<div className="space-y-3">
            <FileDrop accept="image/png,image/jpeg,image/webp" label="Upload a logo" hint="PNG with transparency works best, up to 15 MB" busy={busy} onFile={upload} />
            <div className="flex gap-2"><input className="input" type="url" placeholder="or a logo URL" aria-label="Logo URL" value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); fromUrl(); } }} /><button type="button" onClick={fromUrl} className="btn" disabled={busy}>{busy ? <Spinner size={14} /> : "Fetch"}</button></div>
            {assets.length > 0 && <ul className="flex flex-wrap gap-2">{assets.map((a) => <li key={a.id} className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2 py-1 text-xs"><button type="button" className="max-w-32 truncate text-gray-200" onClick={() => patch({ logo: { asset_id: a.id, position: "top-right", scale: 0.14, opacity: 0.9 } })}>{a.filename || a.id}</button><button type="button" aria-label={`Delete ${a.filename || a.id}`} onClick={() => del(a.id)} className="text-gray-500 hover:text-mirage-rose"><Trash2 size={12} /></button></li>)}</ul>}
          </div>)}
          {err && <p className="mt-2 text-xs text-mirage-rose" role="alert">{err}</p>}
        </div>
        <div className={sec}>
          <p className="label">Voice</p>
          <select className="input" aria-label="Voice" value={o.voice} onChange={(e) => patch({ voice: e.target.value })}>{voices.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}</select>
          <p className="mt-1.5 text-xs text-gray-500">A cloned voice is a synthetic copy of the replica owner&apos;s voice and needs their verified consent. If it ever fails, Mirage falls back to the default voice and logs it.</p>
        </div>
        <div className={sec}>
          <p className="label">Scenes and finishing</p>
          <div className="space-y-3">
            <Toggle checked={o.scenes === "paragraphs"} onChange={(v) => patch({ scenes: v ? "paragraphs" : "single" })} label="Split blank-line paragraphs into scenes" hint={`${sceneCount} scene${sceneCount === 1 ? "" : "s"} detected. Each is rendered separately and joined with the transition.`} />
            {o.scenes === "paragraphs" && sceneCount > 1 && (<div><p className="label">Transition between scenes</p><div className="flex flex-wrap items-center gap-3"><Segmented size="sm" label="Transition" value={o.transition} onChange={(v) => patch({ transition: v })} options={[{ id: "cut", label: "Cut" }, { id: "fade", label: "Fade" }, { id: "dip", label: "Dip to black" }, { id: "slide", label: "Slide" }]} />
              {o.transition !== "cut" && <label className="flex items-center gap-2 text-xs text-gray-400">{o.transition_s.toFixed(1)} s<input type="range" min={1} max={15} value={Math.round(o.transition_s * 10)} aria-label="Transition length" onChange={(e) => patch({ transition_s: Number(e.target.value) / 10 })} className="w-28" /></label>}</div></div>)}
            <Toggle checked={o.thumbnail} onChange={(v) => patch({ thumbnail: v })} label="Create a thumbnail image" />
            <Toggle checked={o.sharpen} onChange={(v) => patch({ sharpen: v })} label="Sharpen the mouth (slower)" hint="Super-resolution on the lip area. Untested at length: expect longer renders." />
          </div>
        </div>
      </div>
      <div className="lg:sticky lg:top-4 lg:self-start">
        <p className="label">Look</p>
        <div className={`mx-auto ${tall ? "max-w-[9rem]" : "max-w-full"}`}>
          <div className="relative overflow-hidden rounded-xl" style={{ aspectRatio: ASPECT[o.format] }} data-testid="look-preview">
            <BgPreview bg={o.bg} faceSrc={faceSrc} aspect={ASPECT[o.format]} />
            <CaptionSample style={o.captions} accent={o.accent} />
            {o.logo && <div className={`absolute grid place-items-center overflow-hidden rounded bg-white/90 text-[8px] font-bold text-ink ${o.logo.position.includes("top") ? "top-2" : "bottom-2"} ${o.logo.position.includes("left") ? "left-2" : "right-2"}`} style={{ width: `${o.logo.scale * 100 * 1.6}%`, aspectRatio: "2/1", opacity: o.logo.opacity }}>
              {logoSrc ? /* eslint-disable-next-line @next/next/no-img-element */ <img src={logoSrc} alt="" className="h-full w-full object-contain" /> : "LOGO"}</div>}
          </div>
        </div>
        <p className="mt-2 text-center text-[11px] text-gray-500">{o.format} - {o.resolution}p{o.captions !== "off" ? ` - ${o.captions} captions` : ""}</p>
      </div>
    </div>
  );
}
