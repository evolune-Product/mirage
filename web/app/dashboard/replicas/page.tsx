"use client";
import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, Cpu, Loader2, Plus, ScanFace, ShieldCheck, Trash2 } from "lucide-react";
import { API_URL, api, Replica, isPhotoReplica } from "@/lib/api";
import CreateReplica from "@/components/replica/CreateReplica";
import { PhotoProgress, usePhoto } from "@/components/replica/PhotoStatus";
import ConsentRecorder from "@/components/ConsentRecorder";
import ReplicaDetail from "@/components/replica/ReplicaDetail";
import { Shell, Badge, Empty, SkeletonCards, CopyButton, ConfirmDialog, toast } from "@/components/ui";

const STEPS_V = [["awaiting_consent", "Consent"], ["training", "Training"], ["ready", "Ready"]] as const;
const PHOTO_STEPS = [["awaiting_consent", "Consent"], ["training", "Animating"], ["ready", "Ready"]] as const;

function Stepper({ status, photo }: { status: string; photo?: boolean }) {
  const STEPS = photo ? PHOTO_STEPS : STEPS_V;
  const idx = status === "error" ? 1 : Math.max(0, STEPS.findIndex(([k]) => k === status));
  return (
    <div className="flex items-center">
      {STEPS.map(([k, label], i) => {
        const done = i < idx || status === "ready"; const cur = i === idx && status !== "ready";
        return (
          <div key={k} className="flex flex-1 items-center last:flex-none">
            <div className="flex flex-col items-center gap-1">
              <span className={`grid h-5 w-5 place-items-center rounded-full text-[10px] ring-1 ring-inset ${done ? "bg-vocalface-mint text-ink ring-vocalface-mint" : cur ? (status === "error" ? "bg-vocalface-rose/20 text-vocalface-rose ring-vocalface-rose" : "bg-vocalface-violet/20 text-white ring-vocalface-violet") : "text-gray-500 ring-white/15"}`}>
                {done ? <Check size={11} /> : cur && status === "training" ? <Loader2 size={11} className="animate-spin" /> : i + 1}
              </span>
              <span className={`text-[10px] ${done || cur ? "text-gray-300" : "text-gray-600"}`}>{label}</span>
            </div>
            {i < STEPS.length - 1 && <span className={`mx-1.5 mb-4 h-px flex-1 ${i < idx || status === "ready" ? "bg-vocalface-mint/60" : "bg-white/10"}`} />}
          </div>
        );
      })}
    </div>
  );
}

function Face({ r }: { r: Replica }) {
  const [bad, setBad] = useState(false); const [src, setSrc] = useState("");
  // Files are served through short-lived signed links (POST /v1/files/sign), not guessable public URLs.
  useEffect(() => { if (r.status !== "ready") return; let live = true;
    api<{ url: string }>("/v1/files/sign", { body: { path: `/v1/files/replicas/${r.id}/face.png` } }).then((j) => { if (live) setSrc(API_URL + j.url); }).catch(() => live && setBad(true));
    return () => { live = false; }; }, [r.id, r.status]);
  return (
    <div className="relative h-16 w-16 shrink-0 overflow-hidden rounded-2xl bg-ink-3 ring-1 ring-white/10">
      {r.status !== "ready" && isPhotoReplica(r)
        // eslint-disable-next-line @next/next/no-img-element
        ? <img src={r.train_video_url} alt={r.name} className="h-full w-full object-cover" />
        : r.status === "ready" && src && !bad
        // eslint-disable-next-line @next/next/no-img-element
        ? <img src={src} alt={r.name} className="h-full w-full object-cover" onError={() => setBad(true)} />
        : <div className="grid h-full w-full place-items-center bg-[radial-gradient(circle_at_30%_20%,rgba(79,111,168,.35),transparent_70%)] text-gray-500"><ScanFace size={26} /></div>}
    </div>
  );
}

function PhotoCard({ r }: { r: Replica }) {
  const { p } = usePhoto(r.id, true);
  if (!p) return null;
  return <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3"><PhotoProgress p={p} replicaStatus={r.status} /></div>;
}

export default function Replicas() {
  const [list, setList] = useState<Replica[] | null>(null);
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<Replica | null>(null); const [del, setDel] = useState<Replica | null>(null);
  const load = useCallback(async () => { try {
    // The API keeps status=awaiting_consent until a worker picks the job up, so ask whether consent exists and show "training" (queued) in that case.
    const l = await api<Replica[]>("/v1/replicas");
    setList(await Promise.all(l.map(async (r) => { if (r.status !== "awaiting_consent") return r; try { const c = await api<{ has_consent: boolean }>(`/v1/replicas/${r.id}/consent`); return c.has_consent ? { ...r, status: "training" } : r; } catch { return r; } })));
  } catch (x) { toast.error(x); setList((l) => l ?? []); } }, []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);

  const [cons, setCons] = useState<{ rid: string; cid: string; phrase: string } | null>(null)
  async function startConsent(rid: string) {
    try { const c = await api<{ challenge_id: string; phrase: string }>(`/v1/replicas/${rid}/consent/challenge`, { method: "POST", body: {} }); setCons({ rid, cid: c.challenge_id, phrase: c.phrase }); }
    catch (x) { toast.error(x); }
  }
  const newBtn = <button className="btn-grad" onClick={() => setOpen(true)}><Plus size={16} />New replica</button>;

  return (
    <Shell title="Replicas" subtitle="A replica is the face and look of your agent, learned from a short video of a consenting person." action={newBtn}>
      {list === null ? <SkeletonCards /> : list.length === 0 ? (
        <Empty kind="replica" title="No replicas yet" hint="Add a short front-facing video, or a single portrait photo. We ask the person to confirm consent before anything is trained or animated." action={newBtn} />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <AnimatePresence initial={false}>
            {list.map((r) => (
              <motion.div layout key={r.id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className={`card flex flex-col gap-4 ${cons?.rid === r.id ? "md:col-span-2 xl:col-span-3" : ""}`}>
                <div className="flex items-start gap-4">
                  <Face r={r} />
                  <div className="min-w-0 flex-1"><p className="truncate font-medium">{r.name}</p><p className="truncate font-mono text-xs text-gray-500">{r.id}</p><div className="mt-2 flex flex-wrap items-center gap-2"><Badge s={r.status} /><button className="text-xs text-vocalface-cyan hover:underline" onClick={() => setDetail(r)}>Details</button></div></div>
                  <button aria-label={`Delete ${r.name}`} onClick={() => setDel(r)} className="rounded-lg p-1.5 text-gray-600 transition hover:bg-vocalface-rose/10 hover:text-vocalface-rose"><Trash2 size={15} /></button>
                </div>
                <Stepper status={r.status} photo={isPhotoReplica(r)} />
                {r.status === "awaiting_consent" && cons?.rid !== r.id && (
                  <button className="btn" onClick={() => startConsent(r.id)}><ShieldCheck size={15} />Give consent</button>
                )}
                {isPhotoReplica(r) && r.status !== "awaiting_consent" && <PhotoCard r={r} />}
                {r.status === "training" && !isPhotoReplica(r) && (
                  <div className="rounded-xl border border-vocalface-cyan/20 bg-vocalface-cyan/5 p-3 text-xs leading-relaxed text-gray-300">
                    <p className="mb-1 flex items-center gap-1.5 font-medium text-vocalface-cyan"><Cpu size={13} />Waiting for a worker</p>
                    Consent is recorded. Training runs in a separate background process, so this replica stays queued until a worker picks it up. Start one from the backend folder:
                    <div className="mt-2 flex items-center justify-between gap-2 rounded-lg bg-black/40 px-2.5 py-1.5 font-mono text-[11px] text-gray-200"><span className="truncate">python workers/run_worker.py</span><CopyButton text="python workers/run_worker.py" label="" /></div>
                  </div>
                )}
                {r.status === "error" && <p className="rounded-xl bg-vocalface-rose/10 p-3 text-xs text-vocalface-rose">Training failed. Check the worker logs, then create the replica again.</p>}
                {cons?.rid === r.id && (
                  <ConsentRecorder photo={isPhotoReplica(r)} rid={cons.rid} challengeId={cons.cid} phrase={cons.phrase} onCancel={() => setCons(null)} onDone={() => { setCons(null); load(); }} />
                )}
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
      <ReplicaDetail replica={detail} onClose={() => setDetail(null)} onDeleted={() => { setDetail(null); load(); }} />
      <ConfirmDialog open={!!del} title="Delete this replica?" body={<>This permanently deletes <b>{del?.name}</b>, its consent evidence and media. This cannot be undone.</>} confirmLabel="Delete replica" onClose={() => setDel(null)}
        onConfirm={async () => { try { await api(`/v1/replicas/${del!.id}`, { method: "DELETE" }); setDel(null); toast.success("Replica deleted."); load(); } catch (x) { toast.error(x); } }} />
      <CreateReplica open={open} onClose={() => setOpen(false)} onCreated={() => { setOpen(false); load(); }} />
    </Shell>
  );
}
