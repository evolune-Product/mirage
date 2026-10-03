"use client";
import { useEffect, useState } from "react";
import { Palette, Save, Trash2 } from "lucide-react";
import { api, errText, signedUrl } from "@/lib/api";
import { Section, Spinner, toast } from "@/components/ui";
import { Callout } from "@/components/kit";
import BackgroundPicker, { Bg, NO_BG, bgBody, bgFromServer } from "@/components/creative/BackgroundPicker";

export default function BackgroundSection({ rid, ready }: { rid: string; ready: boolean }) {
  const [saved, setSaved] = useState<Bg | undefined>(undefined); const [bg, setBg] = useState<Bg>(NO_BG); const [busy, setBusy] = useState(false); const [err, setErr] = useState(""); const [face, setFace] = useState("");
  useEffect(() => {
    api<{ background: Parameters<typeof bgFromServer>[0] }>(`/v1/replicas/${rid}/background`).then((r) => { const b = bgFromServer(r.background); setSaved(b); setBg(b); }).catch(() => { setSaved(NO_BG); setBg(NO_BG); });
    if (ready) signedUrl(`/v1/files/replicas/${rid}/face.png`).then(setFace).catch(() => {});
  }, [rid, ready]);
  const dirty = saved !== undefined && JSON.stringify(saved) !== JSON.stringify(bg);
  async function save() {
    setBusy(true); setErr("");
    try {
      if (bg.type === "none") { if (saved && saved.type !== "none") await api(`/v1/replicas/${rid}/background`, { method: "DELETE" }); }
      else { if (bg.type === "image" && !bg.asset_id) throw new Error("Pick or upload an image first."); await api(`/v1/replicas/${rid}/background`, { body: bgBody(bg) }); }
      setSaved(bg); toast.success(bg.type === "none" ? "Background removed." : "Background saved. Live conversations rebuild their base clip once.");
    } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  return (
    <Section title="Background" icon={<Palette size={16} className="text-mirage-cyan" />} hint="Replaces the background behind this replica in live conversations and in every video (unless a video sets its own). Segmentation runs once, so live speed is unchanged.">
      {saved === undefined ? <div className="h-28 animate-pulse rounded-xl bg-white/5" /> : (<div className="space-y-3">
        <BackgroundPicker value={bg} onChange={setBg} faceSrc={face || undefined} noneLabel="Original" />
        <p className="text-[11px] text-gray-500">The preview is an approximation: the real cut-out is made from your clip, so hair edges and shoulders can differ slightly.</p>
        {err && <Callout tone="bad">{err}</Callout>}
        <div className="flex gap-2"><button type="button" className="btn" disabled={busy || !dirty} onClick={save}>{busy ? <Spinner size={14} /> : bg.type === "none" ? <Trash2 size={14} /> : <Save size={14} />}{bg.type === "none" ? "Remove background" : "Save background"}</button></div>
      </div>)}
    </Section>
  );
}
