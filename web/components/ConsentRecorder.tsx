"use client";
import { useEffect, useRef, useState } from "react";
import { Loader2, Mic, Square, RotateCcw } from "lucide-react";
import { API_URL, api, getKey } from "@/lib/api";
import { toast } from "@/components/ui";

// Typed-phrase fallback exists only for local dev (server flag MIRAGE_ALLOW_TYPED_CONSENT must also be on).
const TYPED_FALLBACK = process.env.NEXT_PUBLIC_ALLOW_TYPED_CONSENT === "1";

type Props = { rid: string; challengeId: string; phrase: string; onDone: () => void; onCancel: () => void };

export default function ConsentRecorder({ rid, challengeId, phrase, onDone, onCancel }: Props) {
  const [who, setWho] = useState("");
  const [state, setState] = useState<"idle" | "recording" | "recorded" | "uploading">("idle");
  const [secs, setSecs] = useState(0);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [err, setErr] = useState<{ message: string; heard?: string } | null>(null);
  const [typed, setTyped] = useState("");
  const rec = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopTracks = () => { stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null; if (timer.current) clearInterval(timer.current); };
  useEffect(() => () => { stopTracks(); }, []);
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

  async function start() {
    setErr(null);
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false } });
      stream.current = s; chunks.current = [];
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m));
      const r = new MediaRecorder(s, mime ? { mimeType: mime } : undefined);
      r.ondataavailable = (e) => { if (e.data.size) chunks.current.push(e.data); };
      r.onstop = () => {
        const b = new Blob(chunks.current, { type: r.mimeType || "audio/webm" });
        setBlob(b); setPreviewUrl(URL.createObjectURL(b)); setState("recorded"); stopTracks();
      };
      rec.current = r; r.start(250); setSecs(0); setState("recording");
      timer.current = setInterval(() => setSecs((x) => x + 1), 1000);
    } catch { toast.error("Microphone unavailable. Allow microphone access in your browser and try again."); }
  }
  const stop = () => { if (rec.current && rec.current.state !== "inactive") rec.current.stop(); };
  const redo = () => { setBlob(null); setPreviewUrl(""); setErr(null); setState("idle"); };

  async function upload() {
    if (!blob) return;
    setState("uploading"); setErr(null);
    const ext = blob.type.includes("mp4") ? "m4a" : "webm";
    const fd = new FormData();
    fd.append("challenge_id", challengeId); fd.append("speaker_name", who); fd.append("file", blob, `consent.${ext}`);
    try {
      const res = await fetch(`${API_URL}/v1/replicas/${rid}/consent/audio`, { method: "POST", headers: { "x-api-key": getKey() }, body: fd });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) {
        const d = j.detail;
        const message = typeof d === "string" ? d : d?.message || d?.error || res.statusText;
        setErr({ message: `${res.status}: ${message}`, heard: d?.heard }); setState("recorded");
        // a failed attempt does not burn the challenge, but a used/expired one needs a new phrase
        if (res.status === 410) toast.error("This phrase expired. Close and start consent again.");
        return;
      }
      toast.success(j.voice_status === "match" ? "Consent verified: phrase and voice match." : "Consent recorded. Training is queued.");
      onDone();
    } catch (x) { setErr({ message: String(x) }); setState("recorded"); }
  }
  async function submitTyped() {
    try { await api(`/v1/replicas/${rid}/consent`, { body: { challenge_id: challengeId, speaker_name: who, audio_url: "dashboard://typed-confirmation", transcript: typed } }); toast.success("Consent recorded (typed, dev mode)."); onDone(); }
    catch (x) { toast.error(x); }
  }

  const mm = String(Math.floor(secs / 60)).padStart(2, "0"), ss = String(secs % 60).padStart(2, "0");
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4" data-testid="consent-recorder">
      <p className="label">Consent for {rid}</p>
      <p className="mb-3 text-sm text-gray-300">The person in the training video must record their own voice reading this phrase aloud. We transcribe it, check the code words, and compare the voice with the training video.</p>
      <p className="mb-3 rounded-lg border border-mirage-amber/20 bg-mirage-amber/5 p-3 font-display text-xl leading-snug" data-testid="consent-phrase">{phrase}</p>
      <div className="grid gap-3 md:grid-cols-[1fr_2fr]">
        <div><label className="label">Your name</label><input className="input" value={who} onChange={(e) => setWho(e.target.value)} /></div>
        <div>
          <label className="label">Recording</label>
          <div className="flex flex-wrap items-center gap-3">
            {state === "idle" && <button className="btn" onClick={start} data-testid="consent-record"><Mic size={15} />Start recording</button>}
            {state === "recording" && <><button className="btn" onClick={stop} data-testid="consent-stop"><Square size={14} />Stop</button><span className="flex items-center gap-2 font-mono text-sm text-mirage-rose"><i className="h-2 w-2 animate-pulse rounded-full bg-mirage-rose" />{mm}:{ss}</span></>}
            {(state === "recorded" || state === "uploading") && <>
              {previewUrl && <audio controls src={previewUrl} className="h-9" />}
              <button className="btn-ghost" onClick={redo} disabled={state === "uploading"}><RotateCcw size={14} />Re-record</button></>}
          </div>
        </div>
      </div>
      {err && <div role="alert" className="mt-3 rounded-lg bg-mirage-rose/10 p-3 text-xs text-mirage-rose" data-testid="consent-error">{err.message}{err.heard ? <span className="mt-1 block text-gray-300">We heard: &ldquo;{err.heard}&rdquo;</span> : null}</div>}
      <div className="mt-4 flex gap-2">
        <button className="btn-grad" disabled={!who.trim() || !blob || state === "uploading"} onClick={upload} data-testid="consent-submit">
          {state === "uploading" ? <><Loader2 size={15} className="animate-spin" />Verifying</> : "Verify and confirm consent"}</button>
        <button className="btn-ghost" onClick={onCancel}>Cancel</button>
      </div>
      {TYPED_FALLBACK && (
        <div className="mt-4 border-t border-white/10 pt-3"><p className="label">Dev only: typed fallback</p>
          <div className="flex gap-2"><input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="type the phrase" /><button className="btn" disabled={!who || !typed} onClick={submitTyped}>Submit typed</button></div></div>
      )}
    </div>
  );
}
