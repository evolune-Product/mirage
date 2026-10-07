"use client";
import { useState } from "react";
import { Camera, Check, Film, X } from "lucide-react";
import { api, errText, markPhotoReplica, Replica } from "@/lib/api";
import { Field, Modal, Spinner, toast } from "@/components/ui";
import { Callout, Segmented } from "@/components/kit";
import { PHOTO_RULES } from "@/components/replica/PhotoStatus";

type Kind = "video" | "photo";

export default function CreateReplica({ open, onClose, onCreated, initial = "video" }: { open: boolean; onClose: () => void; onCreated: (r: Replica, kind: Kind) => void; initial?: Kind }) {
  const [kind, setKind] = useState<Kind>(initial); const [name, setName] = useState(""); const [url, setUrl] = useState(""); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const [idle, setIdle] = useState(4); const [motion, setMotion] = useState(1); const [imgOk, setImgOk] = useState<boolean | null>(null);
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const r = kind === "photo"
        ? await api<Replica>("/v1/replicas/photo", { body: { name, photo_url: url, idle_seconds: idle, head_motion: motion } })
        : await api<Replica>("/v1/replicas", { body: { name, train_video_url: url } });
      if (kind === "photo") markPhotoReplica(r.id);
      setName(""); setUrl(""); setImgOk(null); onCreated(r, kind); toast.success("Replica created. Next: give consent.");
    } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  return (
    <Modal open={open} onClose={onClose} title="New replica">
      <form onSubmit={submit} className="space-y-4">
        <Segmented label="Replica source" value={kind} onChange={(k) => { setKind(k); setErr(""); }} options={[{ id: "video", label: <span className="inline-flex items-center gap-1.5"><Film size={14} />From a video</span> }, { id: "photo", label: <span className="inline-flex items-center gap-1.5"><Camera size={14} />From a photo</span> }]} />
        <Field label="Name"><input className="input" required autoFocus placeholder="e.g. Founder" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        {kind === "video" ? (
          <Field label="Training video URL" hint="A public link to a 1-2 minute video, one face, good light. The voice in it is used to verify consent."><input className="input" type="url" required placeholder="https://..." value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
        ) : (<>
          <Field label="Portrait photo URL" hint="A public link to one JPG/PNG/WebP portrait. Direct upload is not supported by the API, so host the file first.">
            <input className="input" type="url" required placeholder="https://.../portrait.jpg" value={url} onChange={(e) => { setUrl(e.target.value); setImgOk(null); }} aria-label="Photo URL" /></Field>
          {url && (<div className="flex items-center gap-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={url} alt="Photo preview" className={`h-24 w-24 rounded-xl object-cover ring-1 ring-white/10 ${imgOk === false ? "hidden" : ""}`} onLoad={() => setImgOk(true)} onError={() => setImgOk(false)} />
            <p className="text-xs text-gray-400">{imgOk === false ? "The browser cannot load this image. The server may still be able to." : imgOk ? "Check it against the rules below: is the mouth closed?" : "Loading preview..."}</p></div>)}
          <Callout tone="warn" title="The photo must have a closed mouth">
            VocalFace animates the photo into a calm idle loop (blinking, slight head sway) and keeps the mouth exactly as photographed, so a smile with teeth or a mid-word frame would freeze open. The server checks and rejects photos that fail, with the reason.
            <ul className="mt-2 space-y-1">{PHOTO_RULES.map((r) => <li key={r} className="flex gap-1.5"><Check size={12} className="mt-0.5 shrink-0 text-vocalface-mint" />{r}</li>)}</ul></Callout>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={`Idle loop length: ${idle} s`} hint="2-8 s. Longer loops look less repetitive."><input type="range" min={2} max={8} step={1} value={idle} onChange={(e) => setIdle(Number(e.target.value))} className="w-full" aria-label="Idle seconds" /></Field>
            <Field label={`Head motion: ${motion.toFixed(1)}`} hint="0 = still, 1 = natural, 2 = lively."><input type="range" min={0} max={2} step={0.1} value={motion} onChange={(e) => setMotion(Number(e.target.value))} className="w-full" aria-label="Head motion" /></Field>
          </div>
          <p className="text-xs text-gray-500">A photo has no voice, so voice matching is skipped and a preset voice is used. The person in the photo still has to give consent.</p>
        </>)}
        {err && <Callout tone="bad" title="The server rejected this"><span data-testid="create-error" className="flex items-start justify-between gap-2 text-gray-200"><span>{err}</span><button type="button" aria-label="Dismiss" onClick={() => setErr("")}><X size={13} /></button></span></Callout>}
        <button className="btn-grad w-full" disabled={busy}>{busy && <Spinner />}Create replica</button>
      </form>
    </Modal>
  );
}
