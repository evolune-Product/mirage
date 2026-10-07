"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { AudioLines, Play, RefreshCw, Trash2, Volume2 } from "lucide-react";
import { api, apiBlobUrl, errText, VoiceClone, Lang } from "@/lib/api";
import { Badge, ConfirmDialog, Field, Section, Spinner, toast } from "@/components/ui";
import { Callout, Progress } from "@/components/kit";

/** Plain-language reading of the WeSpeaker similarity (docs/overnight/voice-cloning.md: same person 0.8-0.9, different person 0.0-0.4, clone must be >= 0.30). */
export function simVerdict(sim: number): { label: string; tone: "mint" | "grad" | "rose"; text: string } {
  if (sim >= 0.75) return { label: "Very close", tone: "mint", text: "Sounds like the same person to the speaker model." };
  if (sim >= 0.55) return { label: "Good", tone: "mint", text: "Recognisably the same voice; fine for videos." };
  if (sim >= 0.3) return { label: "Weak", tone: "grad", text: "Accepted, but it only loosely resembles the original. Re-clone from cleaner training audio for better results." };
  return { label: "Too low", tone: "rose", text: "Below the 0.30 threshold, so VocalFace refuses to use this voice." };
}

export default function VoicePanel({ rid, replicaReady, consent }: { rid: string; replicaReady: boolean; consent: "none" | "typed" | "verified" | undefined }) {
  const [v, setV] = useState<VoiceClone | null | undefined>(undefined); const [na, setNa] = useState(false); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const [text, setText] = useState("Hello, this is a quick test of my cloned voice."); const [lang, setLang] = useState("en"); const [langs, setLangs] = useState<Lang[]>([]);
  const [audio, setAudio] = useState(""); const [pbusy, setPbusy] = useState(false); const [del, setDel] = useState(false); const prev = useRef("");
  const load = useCallback(() => api<VoiceClone>(`/v1/replicas/${rid}/voice`).then((x) => { setV(x); setNa(false); }).catch((x: Error) => { if (/404: Not Found$/.test(x.message)) setNa(true); setV(null); }), [rid]);
  useEffect(() => { setV(undefined); setAudio(""); load(); api<Lang[]>("/v1/languages").then((l) => setLangs(l.filter((x) => x.tts))).catch(() => {}); }, [load]);
  const active = v?.status === "queued" || v?.status === "processing";
  useEffect(() => { if (!active) return; const t = setInterval(load, 3000); return () => clearInterval(t); }, [active, load]);
  useEffect(() => { prev.current = audio; return () => { if (prev.current) URL.revokeObjectURL(prev.current); }; }, [audio]);

  async function clone(force = false) {
    setBusy(true); setErr("");
    try { setV(await api<VoiceClone>(`/v1/replicas/${rid}/voice`, { body: { force } })); toast.info("Voice cloning started. This takes under a minute."); }
    catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  async function preview(e: React.FormEvent) {
    e.preventDefault(); setPbusy(true);
    try { setAudio(await apiBlobUrl(`/v1/replicas/${rid}/voice/preview`, { text, language: lang })); } catch (x) { toast.error(x); } finally { setPbusy(false); }
  }
  const sv = v && v.similarity != null ? simVerdict(v.similarity) : null;
  const needVerified = consent !== "verified";
  return (
    <Section title="Cloned voice" icon={<AudioLines size={16} className="text-vocalface-violet" />} hint="A synthetic copy of the replica owner's voice, created only with their recorded consent. It speaks your videos and can be picked for personas.">
      {na ? <Callout title="Unavailable">This server does not expose voice cloning.</Callout> : v === undefined ? <div className="h-16 animate-pulse rounded-xl bg-white/5" /> : (<div className="space-y-3" data-testid="voice-panel">
        {(!v || v.status === "none") && (<>
          {!replicaReady
            ? <Callout tone="warn" title="Replica not ready yet">Cloning starts from the training video&apos;s audio, so the replica has to finish training first. If consent is voice-verified VocalFace starts cloning automatically once it is ready.</Callout>
            : needVerified
              ? <Callout tone="warn" title="Voice-verified consent needed">{consent === "none" ? "This replica has no consent on file. " : "Only typed consent is on file. "}To clone a voice, the person must record the consent phrase with a microphone and their voice has to match the training video. Open Consent evidence above to check, or re-record consent from the replica card.</Callout>
              : <Callout tone="info" title="Ready to clone">VocalFace extracts the cleanest 15-20 seconds of the training audio and tests the result. Good for videos; in live conversations a cloned voice adds roughly 4-5 s of delay, so presets stay the default there.</Callout>}
          <button type="button" className="btn-grad" disabled={busy || !replicaReady || needVerified} onClick={() => clone(false)}>{busy ? <Spinner /> : <AudioLines size={15} />}Clone this voice</button>
          {err && <Callout tone="bad" title="Cloning refused">{err}</Callout>}
        </>)}
        {active && (<div className="rounded-xl border border-vocalface-violet/25 bg-vocalface-violet/[0.07] p-3.5"><p className="mb-2 flex items-center gap-2 text-sm"><Spinner size={14} />{v.status === "queued" ? "Queued" : "Cloning your voice"}</p><Progress pct={v.status === "queued" ? 15 : 60} label="Extracting a clean reference, synthesising a test sentence, measuring similarity..." /></div>)}
        {v?.status === "failed" && (<><Callout tone="bad" title="Cloning failed">{v.error || "The clone did not pass the quality check."}</Callout>
          <button type="button" className="btn" disabled={busy} onClick={() => clone(true)}>{busy ? <Spinner size={14} /> : <RefreshCw size={14} />}Try again</button></>)}
        {v?.status === "revoked" && <Callout tone="warn" title="Voice revoked">Consent was revoked, so the reference audio was deleted and this voice can no longer be used. Record consent again to clone a new one.</Callout>}
        {v?.status === "ready" && (<>
          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
            <div className="flex flex-wrap items-center gap-2"><Badge s="ready" /><span className="rounded-full bg-vocalface-violet/15 px-2 py-0.5 text-[11px] text-vocalface-violet">synthetic voice</span><code className="ml-auto truncate font-mono text-[11px] text-gray-500">{v.voice_id}</code></div>
            {sv && v.similarity != null && (<div className="mt-3" data-testid="similarity">
              <div className="flex items-baseline justify-between"><p className="text-sm">Similarity <span className="font-mono text-white">{v.similarity.toFixed(2)}</span> <span className="text-gray-400">({sv.label})</span></p><span className="text-[11px] text-gray-500">same person is typically 0.8-0.9</span></div>
              <Progress pct={v.similarity * 100} tone={sv.tone} className="mt-1.5" label={sv.text} /></div>)}
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-gray-400 sm:grid-cols-4">
              <div><dt>Reference</dt><dd className="font-mono text-gray-200">{v.reference_seconds ?? "-"} s</dd></div>
              <div><dt>Noise (SNR)</dt><dd className="font-mono text-gray-200">{v.reference_quality?.snr_db != null ? `${v.reference_quality.snr_db.toFixed(0)} dB` : "-"}</dd></div>
              <div><dt>Word errors</dt><dd className="font-mono text-gray-200">{v.wer != null ? `${(v.wer * 100).toFixed(0)}%` : "-"}</dd></div>
              <div><dt>Speed</dt><dd className="font-mono text-gray-200">{v.synth_rtf != null ? `${v.synth_rtf.toFixed(1)}x` : "-"}</dd></div></dl>
          </div>
          <form onSubmit={preview} className="space-y-2.5 rounded-xl bg-white/[0.03] p-3.5">
            <Field label="Preview text"><input className="input" maxLength={300} required value={text} onChange={(e) => setText(e.target.value)} aria-label="Preview text" /></Field>
            <div className="flex flex-wrap items-end gap-2">
              <div className="w-40"><label className="label">Language</label><select className="input" aria-label="Preview language" value={lang} onChange={(e) => setLang(e.target.value)}>{(langs.length ? langs : [{ code: "en", name: "English" } as Lang]).map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}</select></div>
              <button className="btn" disabled={pbusy}>{pbusy ? <Spinner size={14} /> : <Play size={14} />}Play preview</button></div>
            {audio && <audio controls autoPlay src={audio} className="h-9 w-full" aria-label="Cloned voice preview" />}
            <p className="flex items-center gap-1.5 text-[11px] text-gray-500"><Volume2 size={11} />Previews are moderated and labelled as synthetic.</p>
          </form>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-ghost !px-3 !py-1.5 text-xs" disabled={busy} onClick={() => clone(true)}><RefreshCw size={13} />Re-clone</button>
            <button type="button" className="btn-ghost !border-vocalface-rose/40 !px-3 !py-1.5 text-xs !text-vocalface-rose hover:!bg-vocalface-rose/10" onClick={() => setDel(true)}><Trash2 size={13} />Delete voice</button></div>
        </>)}
        {err && v && v.status !== "none" && <Callout tone="bad">{err}</Callout>}
      </div>)}
      <ConfirmDialog open={del} title="Delete the cloned voice?" body="This removes the reference audio and the voice. Personas that use it fall back to a preset voice. The replica and its consent are untouched." confirmLabel="Delete voice" onClose={() => setDel(false)}
        onConfirm={async () => { try { await api(`/v1/replicas/${rid}/voice`, { method: "DELETE" }); setDel(false); setAudio(""); toast.success("Voice deleted."); load(); } catch (x) { toast.error(x); } }} />
    </Section>
  );
}
