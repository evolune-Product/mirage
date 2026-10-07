"use client";
import { useEffect, useRef, useState } from "react";
import { Camera, Loader2, Square, RotateCcw } from "lucide-react";
import { API_URL, api, getKey } from "@/lib/api";
import { toast } from "@/components/ui";

// Typed-phrase / audio-only fallback exists only for local dev (server flags VOCALFACE_ALLOW_TYPED_CONSENT and
// VOCALFACE_CONSENT_FACE_MATCH=warn must also allow it; production rejects both).
const TYPED_FALLBACK = process.env.NEXT_PUBLIC_ALLOW_TYPED_CONSENT === "1";
const MIN_SECONDS = 4;

type Props = { rid: string; challengeId: string; phrase: string; onDone: () => void; onCancel: () => void; photo?: boolean };
type Problem = { message: string; hint?: string; heard?: string; reasons?: string[] };

// What each server error means for the person standing in front of the camera.
const HINTS: Record<string, string> = {
  face_no_video: "The recording did not contain video. Allow camera access and record again.",
  face_no_face: "We need one clear face for the whole recording: face the camera, good light, nobody else in frame, nothing covering your face.",
  face_mismatch: "The face in the recording does not look like the face in the replica's source. Only the person shown in the photo or video can give consent.",
  face_no_reference: "We could not find a clear face in the replica's photo or training video. Replace the source with a clear, front-facing one.",
  face_unavailable: "Face verification is temporarily unavailable. Try again in a minute.",
  liveness_failed: "We could not confirm a live person. Look at the camera and speak naturally; a photo or screen held up to the camera is rejected.",
  phrase_mismatch: "Read the phrase exactly as shown, including the three code words, at a normal pace.",
  voice_mismatch: "The voice in the recording does not match the voice in the training video.",
};

export default function ConsentRecorder({ rid, challengeId, phrase, onDone, onCancel, photo }: Props) {
  const [who, setWho] = useState("");
  const [state, setState] = useState<"idle" | "starting" | "recording" | "recorded" | "uploading">("idle");
  const [secs, setSecs] = useState(0);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [err, setErr] = useState<Problem | null>(null);
  const [typed, setTyped] = useState("");
  const rec = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const live = useRef<HTMLVideoElement | null>(null);

  const stopTracks = () => { stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null; if (timer.current) clearInterval(timer.current); if (live.current) live.current.srcObject = null; };
  useEffect(() => () => { stopTracks(); }, []);
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

  async function start() {
    setErr(null); setState("starting");
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error("no mediaDevices"), { name: "NotSupportedError" });
      const s = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 24 }, facingMode: "user" },
        audio: { echoCancellation: false, noiseSuppression: false },
      });
      stream.current = s; chunks.current = [];
      if (live.current) { live.current.srcObject = s; live.current.play().catch(() => {}); }
      const mime = ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"].find((m) => MediaRecorder.isTypeSupported(m));
      const r = new MediaRecorder(s, mime ? { mimeType: mime, videoBitsPerSecond: 800_000 } : undefined);
      r.ondataavailable = (e) => { if (e.data.size) chunks.current.push(e.data); };
      r.onstop = () => {
        const b = new Blob(chunks.current, { type: r.mimeType || "video/webm" });
        setBlob(b); setPreviewUrl(URL.createObjectURL(b)); setState("recorded"); stopTracks();
      };
      rec.current = r; r.start(250); setSecs(0); setState("recording");
      timer.current = setInterval(() => setSecs((x) => x + 1), 1000);
    } catch (x) {
      stopTracks(); setState("idle");
      const name = (x as { name?: string })?.name;
      const message =
        name === "NotAllowedError" || name === "SecurityError" ? "Camera or microphone permission was denied."
        : name === "NotFoundError" || name === "OverconstrainedError" ? "No camera or microphone was found."
        : name === "NotReadableError" ? "The camera or microphone is in use by another app."
        : name === "NotSupportedError" ? "This browser cannot record from the camera (use HTTPS or localhost, and a current Chrome, Edge, Safari or Firefox)."
        : "Could not start the camera and microphone.";
      setErr({ message, hint: "Consent needs both your face and your voice. Allow access in the browser's site settings, then press Start again." });
    }
  }
  const stop = () => { if (rec.current && rec.current.state !== "inactive") rec.current.stop(); };
  const redo = () => { setBlob(null); setPreviewUrl(""); setErr(null); setState("idle"); };

  async function upload() {
    if (!blob) return;
    setState("uploading"); setErr(null);
    const ext = blob.type.includes("mp4") ? "mp4" : "webm";
    const fd = new FormData();
    fd.append("challenge_id", challengeId); fd.append("speaker_name", who); fd.append("file", blob, `consent.${ext}`);
    try {
      const res = await fetch(`${API_URL}/v1/replicas/${rid}/consent/audio`, { method: "POST", headers: { "x-api-key": getKey() }, body: fd });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) {
        const d = j.detail;
        const code = typeof d === "object" ? d?.error : undefined;
        const message = typeof d === "string" ? d : d?.message || d?.error || res.statusText;
        setErr({ message: `${res.status}: ${message}`, hint: code ? HINTS[code] : undefined, heard: d?.heard, reasons: d?.reasons });
        setState("recorded");
        // a failed attempt does not burn the challenge, but a used/expired one needs a new phrase
        if (res.status === 410) toast.error("This phrase expired. Close and start consent again.");
        return;
      }
      toast.success(j.face_status === "match" ? "Consent verified: phrase, voice and face." : j.voice_status === "match" ? "Consent verified: phrase and voice match." : "Consent recorded. Training is queued.");
      onDone();
    } catch (x) { setErr({ message: String(x) }); setState("recorded"); }
  }
  async function submitTyped() {
    try { await api(`/v1/replicas/${rid}/consent`, { body: { challenge_id: challengeId, speaker_name: who, audio_url: "dashboard://typed-confirmation", transcript: typed } }); toast.success("Consent recorded (typed, dev mode)."); onDone(); }
    catch (x) { toast.error(x); }
  }

  const mm = String(Math.floor(secs / 60)).padStart(2, "0"), ss = String(secs % 60).padStart(2, "0");
  const src = photo ? "photo" : "photo or training video";
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4" data-testid="consent-recorder">
      <p className="label">Consent for {rid}</p>
      <p className="mb-2 text-sm text-gray-300">The person shown in the {src} must record a short selfie video reading this phrase aloud. We transcribe it, check the code words, compare your voice and your face with the replica&apos;s source, and check that a live person is speaking.</p>
      <p className="mb-3 text-xs text-gray-400">Privacy: the video is analysed once and then discarded. We keep the audio, a hash of the recording, and match scores, never a stored selfie video. Delete everything any time with Delete replica.</p>
      <p className="mb-3 rounded-lg border border-vocalface-amber/20 bg-vocalface-amber/5 p-3 font-display text-xl leading-snug" data-testid="consent-phrase">{phrase}</p>
      <div className="grid gap-3 md:grid-cols-[1fr_2fr]">
        <div><label className="label">Your name</label><input className="input" value={who} onChange={(e) => setWho(e.target.value)} /></div>
        <div>
          <label className="label">Selfie video</label>
          <div className="relative w-full max-w-xs overflow-hidden rounded-lg border border-white/10 bg-black/40" style={{ aspectRatio: "4 / 3" }}>
            {/* live camera while recording / starting; the recorded clip afterwards */}
            <video ref={live} muted playsInline data-testid="consent-live" className="h-full w-full object-cover" style={{ transform: "scaleX(-1)", display: state === "recording" || state === "starting" ? "block" : "none" }} />
            {(state === "recorded" || state === "uploading") && previewUrl && <video controls playsInline src={previewUrl} data-testid="consent-preview" className="h-full w-full object-cover" />}
            {state === "idle" && <div className="flex h-full items-center justify-center text-xs text-gray-400"><Camera size={18} className="mr-2" />Camera preview appears here</div>}
            {state === "recording" && <span className="absolute left-2 top-2 flex items-center gap-2 rounded bg-black/60 px-2 py-0.5 font-mono text-xs text-vocalface-rose"><i className="h-2 w-2 animate-pulse rounded-full bg-vocalface-rose" />REC {mm}:{ss}</span>}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            {(state === "idle" || state === "starting") && <button className="btn" onClick={start} disabled={state === "starting"} data-testid="consent-record"><Camera size={15} />{state === "starting" ? "Starting camera..." : "Start recording"}</button>}
            {state === "recording" && <><button className="btn" onClick={stop} disabled={secs < MIN_SECONDS} data-testid="consent-stop"><Square size={14} />Stop</button>
              <span className="text-xs text-gray-400">{secs < MIN_SECONDS ? `Keep reading... (min ${MIN_SECONDS}s)` : "Stop when you finish the phrase"}</span></>}
            {(state === "recorded" || state === "uploading") && <button className="btn-ghost" onClick={redo} disabled={state === "uploading"}><RotateCcw size={14} />Re-record</button>}
          </div>
        </div>
      </div>
      {err && <div role="alert" className="mt-3 rounded-lg bg-vocalface-rose/10 p-3 text-xs text-vocalface-rose" data-testid="consent-error">{err.message}
        {err.hint ? <span className="mt-1 block text-gray-300">{err.hint}</span> : null}
        {err.reasons?.length ? <span className="mt-1 block text-gray-400">{err.reasons.join("; ")}</span> : null}
        {err.heard ? <span className="mt-1 block text-gray-300">We heard: &ldquo;{err.heard}&rdquo;</span> : null}</div>}
      <div className="mt-4 flex gap-2">
        <button className="btn-grad" disabled={!who.trim() || !blob || state === "uploading"} onClick={upload} data-testid="consent-submit">
          {state === "uploading" ? <><Loader2 size={15} className="animate-spin" />Verifying face, voice and phrase</> : "Verify and confirm consent"}</button>
        <button className="btn-ghost" onClick={onCancel}>Cancel</button>
      </div>
      {TYPED_FALLBACK && (
        <div className="mt-4 border-t border-white/10 pt-3"><p className="label">Dev only: typed fallback (no face or voice check)</p>
          <div className="flex gap-2"><input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="type the phrase" /><button className="btn" disabled={!who || !typed} onClick={submitTyped}>Submit typed</button></div></div>
      )}
    </div>
  );
}
