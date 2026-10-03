"use client";
import { useCallback, useEffect, useState } from "react";
import { ShieldCheck, Film, Trash2, Mic, Upload, AlertCircle } from "lucide-react";
import { api, Replica, fetchBlobUrl, fmtDate, fmtDur, isPhotoReplica } from "@/lib/api";
import PhotoSection from "@/components/replica/PhotoStatus";
import VoicePanel from "@/components/replica/VoicePanel";
import BackgroundSection from "@/components/replica/BackgroundSection";
import { Modal, Badge, Section, Field, Spinner, ConfirmDialog, toast } from "@/components/ui";

type Rec = { id: string; speaker_name: string; phrase: string; transcript: string; verified_by: string; revoked: boolean; created_at: string };
type Ver = { phrase_score: number; code_words_ok: boolean; voice_score: number | null; voice_status: string };
type Clip = { source_url: string; duration_s: number; width: number; height: number; fps: number; bytes: number; updated_at: string };
const VOICE: Record<string, [string, string]> = { match: ["completed", "Voice matches the training video"], mismatch: ["error", "Voice does not match"], skipped: ["queued", "Voice check skipped"], unavailable: ["queued", "Voice model unavailable"], no_speech: ["error", "No speech detected"] };

function Evidence({ rid, rec }: { rid: string; rec: Rec }) {
  const [v, setV] = useState<Ver | null | undefined>(undefined); const [audio, setAudio] = useState(""); const [aerr, setAerr] = useState(false);
  useEffect(() => {
    let live = true; let url = "";
    api<Ver>(`/v1/replicas/${rid}/consent/${rec.id}/verification`).then((x) => live && setV(x)).catch(() => live && setV(null));
    fetchBlobUrl(`/v1/replicas/${rid}/consent/${rec.id}/audio`).then((u) => { url = u; if (live) setAudio(u); }).catch(() => live && setAerr(true));
    return () => { live = false; if (url) URL.revokeObjectURL(url); };
  }, [rid, rec.id]);
  const [tone, text] = v ? VOICE[v.voice_status] ?? ["queued", v.voice_status] : ["queued", ""];
  return (
    <li className={`rounded-xl border border-white/10 bg-white/[0.03] p-3.5 ${rec.revoked ? "opacity-60" : ""}`}>
      <div className="flex flex-wrap items-center gap-2"><ShieldCheck size={15} className="text-mirage-mint" /><span className="text-sm font-medium">{rec.speaker_name}</span>{rec.revoked && <Badge s="revoked" />}<span className="ml-auto text-xs text-gray-500">{fmtDate(rec.created_at)}</span></div>
      <p className="mt-2 text-xs italic text-gray-400">&ldquo;{rec.transcript}&rdquo;</p>
      {v === undefined ? <div className="mt-3 h-8 animate-pulse rounded bg-white/5" /> : v ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs" data-testid="verification">
          <Badge s={tone} /><span className="text-gray-400">{text}</span>
          {v.voice_score != null && <span className="font-mono text-gray-200" data-testid="voice-score">voice match {(v.voice_score * 100).toFixed(0)}%</span>}
          <span className="font-mono text-gray-400">phrase {(v.phrase_score * 100).toFixed(0)}%</span>
          <span className={v.code_words_ok ? "text-mirage-mint" : "text-mirage-rose"}>{v.code_words_ok ? "code words ok" : "code words missing"}</span>
        </div>
      ) : <p className="mt-3 text-xs text-gray-500">Typed consent ({rec.verified_by}): no recording or voice check on file.</p>}
      {audio ? <audio controls src={audio} className="mt-3 h-9 w-full" /> : v && !aerr ? <div className="mt-3 h-9 animate-pulse rounded bg-white/5" /> : null}
    </li>
  );
}

function ListeningClip({ rid }: { rid: string }) {
  const [clip, setClip] = useState<Clip | null | undefined>(undefined); const [na, setNa] = useState(false); const [url, setUrl] = useState(""); const [busy, setBusy] = useState(false);
  const load = useCallback(() => api<Clip>(`/v1/replicas/${rid}/listening-clip`).then((c) => { setClip(c); }).catch((x: Error) => { if (/Not Found$/.test(x.message) && !/no listening/.test(x.message)) setNa(true); setClip(null); }), [rid]);
  useEffect(() => { setClip(undefined); load(); }, [load]);
  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try { setClip(await api<Clip>(`/v1/replicas/${rid}/listening-clip`, { body: { url } })); setUrl(""); toast.success("Listening clip saved."); }
    catch (x) { if (/404: Not Found$/.test((x as Error).message)) setNa(true); else toast.error(x); } finally { setBusy(false); }
  }
  async function remove() { try { await api(`/v1/replicas/${rid}/listening-clip`, { method: "DELETE" }); setClip(null); toast.success("Listening clip removed."); } catch (x) { toast.error(x); } }
  return (
    <Section title="Listening clip" icon={<Film size={16} className="text-mirage-cyan" />} hint="A short silent clip (a few seconds, mouth closed, natural blinking) the face loops while the agent listens. Without one, the playground falls back to the training video.">
      {na ? <p className="flex items-start gap-2 rounded-xl border border-white/15 bg-white/[0.03] p-3.5 text-sm text-gray-400" data-testid="clip-unavailable"><AlertCircle size={16} className="mt-0.5 shrink-0" />Listening clips are unavailable on this server (it does not expose the endpoint yet).</p> : (<>
        {clip === undefined ? <div className="h-12 animate-pulse rounded-xl bg-white/5" /> : clip ? (
          <div className="mb-3 rounded-xl border border-white/10 bg-white/[0.03] p-3.5 text-sm" data-testid="clip-info">
            <p className="flex items-center gap-2"><Badge s="active" /><span className="font-mono text-xs text-gray-300">{fmtDur(clip.duration_s)} - {clip.width}x{clip.height} - {clip.fps} fps - {(clip.bytes / 1e6).toFixed(1)} MB</span></p>
            <p className="mt-1.5 truncate text-xs text-gray-500">{clip.source_url}</p>
            <button className="mt-2 inline-flex items-center gap-1 text-xs text-mirage-rose hover:underline" onClick={remove}><Trash2 size={12} />Remove clip</button>
          </div>
        ) : <p className="mb-3 rounded-xl border border-dashed border-white/15 p-3.5 text-sm text-gray-500">No listening clip set.</p>}
        <form onSubmit={save} className="space-y-2">
          <Field label={clip ? "Replace with a new clip URL" : "Clip URL"} hint="The server downloads it (video, 1-120 s, max 200 MB). Direct upload from the browser is not supported by the API."><input className="input" type="url" required placeholder="https://.../listening.mp4" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
          <button className="btn" disabled={busy}>{busy ? <Spinner size={14} /> : <Upload size={14} />}Save clip</button>
        </form></>)}
    </Section>
  );
}

export default function ReplicaDetail({ replica: base, onClose, onDeleted }: { replica: Replica | null; onClose: () => void; onDeleted: () => void }) {
  const [recs, setRecs] = useState<Rec[] | null>(null); const [del, setDel] = useState(false); const [fresh, setFresh] = useState<Replica | null>(null);
  const replica = fresh && base && fresh.id === base.id ? fresh : base;
  // Keep the drawer alive: refresh the replica itself (status) and its consent every 5 s while it is open and not finished.
  useEffect(() => { setFresh(null); }, [base?.id]);
  useEffect(() => {
    if (!base) return; let live = true;
    const tick = async () => {
      try {
        const [r, c] = await Promise.all([api<Replica>(`/v1/replicas/${base.id}`), api<{ records: Rec[] }>(`/v1/replicas/${base.id}/consent`)]);
        if (!live) return; setRecs(c.records); setFresh(r.status === "awaiting_consent" && c.records.some((x) => !x.revoked) ? { ...r, status: "training" } : r);
      } catch { if (live) setRecs((x) => x ?? []); }
    };
    setRecs(null); tick(); const t = setInterval(tick, 5000); return () => { live = false; clearInterval(t); };
  }, [base?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const active = (recs ?? []).filter((r) => !r.revoked);
  const consentKind = recs === null ? undefined : active.some((r) => /voice-match/.test(r.verified_by)) ? "verified" : active.length ? "typed" : "none";
  const photo = replica ? isPhotoReplica(replica) : false;
  return (
    <Modal side open={!!replica} onClose={onClose} title={replica?.name ?? "Replica"}>
      {replica && (<>
        <div className="mb-6 flex flex-wrap items-center gap-2"><Badge s={replica.status} /><span className="font-mono text-xs text-gray-500">{replica.id}</span><span className="text-xs text-gray-500">created {fmtDate(replica.created_at)}</span></div>
        {photo && <PhotoSection rid={replica.id} status={replica.status} trainUrl={replica.train_video_url} />}
        <Section title="Consent evidence" icon={<Mic size={16} className="text-mirage-mint" />} hint="The recording, transcript match and voice comparison with the training video for each consent given.">
          {recs === null ? <div className="h-20 animate-pulse rounded-xl bg-white/5" /> : recs.length === 0 ? <p className="rounded-xl border border-dashed border-white/15 p-3.5 text-sm text-gray-500">No consent recorded yet.</p>
            : <ul className="space-y-2.5">{recs.map((r) => <Evidence key={r.id} rid={replica.id} rec={r} />)}</ul>}
        </Section>
        {!photo && <VoicePanel rid={replica.id} replicaReady={replica.status === "ready"} consent={consentKind} />}
        <BackgroundSection rid={replica.id} ready={replica.status === "ready"} />
        {!photo && <ListeningClip rid={replica.id} />}
        <div className="mt-8 border-t border-white/10 pt-5"><p className="text-sm font-medium text-mirage-rose">Danger zone</p>
          <p className="mb-3 mt-1 text-xs text-gray-400">Deleting removes the replica, its consent records and media. Personas using it fall back to voice only.</p>
          <button className="btn-ghost !border-mirage-rose/40 !text-mirage-rose hover:!bg-mirage-rose/10" onClick={() => setDel(true)}><Trash2 size={14} />Delete replica</button></div>
        <ConfirmDialog open={del} title="Delete this replica?" body={<>This permanently deletes <b>{replica.name}</b> and its consent evidence. This cannot be undone.</>} confirmLabel="Delete replica" onClose={() => setDel(false)}
          onConfirm={async () => { try { await api(`/v1/replicas/${replica.id}`, { method: "DELETE" }); setDel(false); toast.success("Replica deleted."); onDeleted(); } catch (x) { toast.error(x); } }} />
      </>)}
    </Modal>
  );
}
