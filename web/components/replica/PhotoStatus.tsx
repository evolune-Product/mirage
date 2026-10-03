"use client";
import { useCallback, useEffect, useState } from "react";
import { Camera } from "lucide-react";
import { api, PhotoStatus as PS, signedUrl } from "@/lib/api";
import { Section, Spinner } from "@/components/ui";
import { Callout, Progress } from "@/components/kit";

export const PHOTO_RULES = ["Exactly one face, looking at the camera", "Mouth closed (a neutral, relaxed expression)", "Eyes open, good even light", "At least 90 px wide on the face; a clear 512 px+ portrait is better"];

/** Compact poll hook shared by the replica card and the detail drawer. */
export function usePhoto(rid: string, enabled: boolean) {
  const [p, setP] = useState<PS | null | undefined>(undefined);
  const load = useCallback(() => api<PS>(`/v1/replicas/${rid}/photo`).then(setP).catch(() => setP(null)), [rid]);
  useEffect(() => { if (!enabled) return; load(); }, [enabled, load]);
  const live = p?.status === "queued" || p?.status === "animating";
  useEffect(() => { if (!live) return; const t = setInterval(load, 4000); return () => clearInterval(t); }, [live, load]);
  return { p, reload: load };
}

export function PhotoProgress({ p, replicaStatus }: { p: PS; replicaStatus: string }) {
  const consentWait = replicaStatus === "awaiting_consent";
  if (p.status === "error") return <Callout tone="bad" title="This photo cannot be animated"><span data-testid="photo-error" className="block text-gray-200">{p.error || "The photo was rejected."}</span><span className="mt-2 block text-gray-400">Fix it and create the replica again with a new photo. Needed: {PHOTO_RULES.slice(0, 3).join("; ").toLowerCase()}.</span></Callout>;
  if (p.status === "ready") return <p className="text-xs text-mirage-mint">Photo animated{p.animate_s ? ` in ${Math.round(p.animate_s)} s` : ""}. The idle loop below is what your agent shows while listening.</p>;
  return (
    <div data-testid="photo-progress">
      <p className="mb-1.5 flex items-center gap-2 text-xs text-gray-300">{consentWait ? "Waiting for consent before animation starts" : <><Spinner size={13} />{p.status === "queued" ? "Queued for the worker" : "Animating your photo (blinking, subtle head motion)"}</>}</p>
      <Progress pct={consentWait ? 5 : p.status === "queued" ? 15 : 55} label={consentWait ? "Consent comes first: nothing is processed until the person agrees." : "One-time step, about 2-3 minutes on an M1-class machine. The page updates by itself."} />
    </div>
  );
}

export default function PhotoSection({ rid, status, trainUrl }: { rid: string; status: string; trainUrl: string }) {
  const { p } = usePhoto(rid, true); const [clip, setClip] = useState("");
  useEffect(() => { if (p?.idle_url) signedUrl(p.idle_url).then(setClip).catch(() => {}); }, [p?.idle_url]);
  if (p === undefined) return <div className="mb-6 h-16 animate-pulse rounded-xl bg-white/5" />;
  if (p === null) return null; // not a photo replica
  return (
    <Section title="Photo avatar" icon={<Camera size={16} className="text-mirage-amber" />} hint="Animated from one portrait. No voice is cloned from a photo: a preset voice is used.">
      <div className="grid gap-3 sm:grid-cols-[8rem_1fr]">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={trainUrl} alt="Source photo" className="aspect-square w-32 rounded-xl object-cover ring-1 ring-white/10" onError={(e) => { (e.target as HTMLImageElement).style.visibility = "hidden"; }} />
        <div className="space-y-3"><PhotoProgress p={p} replicaStatus={status} />
          {p.warnings.length > 0 && <Callout tone="warn" title="Warnings">{p.warnings.map((w) => <span key={w} className="block">{w}</span>)}</Callout>}
          {clip && <video src={clip} muted loop autoPlay playsInline controls className="aspect-video max-h-44 rounded-xl bg-black" aria-label="Idle loop" />}</div>
      </div>
    </Section>
  );
}
