"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Camera, Check, Film, MessagesSquare, Play, ShieldCheck } from "lucide-react";
import { API_URL, api, errText, getKey, Instantiated, markPhotoReplica, Replica, saveId, Conversation } from "@/lib/api";
import { Badge, Field, Shell, Spinner, toast } from "@/components/ui";
import { Callout, Progress, Segmented } from "@/components/kit";
import ConsentRecorder from "@/components/ConsentRecorder";
import TemplatePicker, { CreatedSummary, useTemplates } from "@/components/templates/TemplatePicker";
import { PHOTO_RULES } from "@/components/replica/PhotoStatus";

const STEPS = ["Template", "Replica", "Consent", "Persona", "Test"] as const;
const KEY = "mirage_onboarding";
type St = { step: number; tpl: string; rid: string; photo: boolean; pid: string; done: boolean };
const load = (): St => { try { return { step: 0, tpl: "", rid: "", photo: false, pid: "", done: false, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch { return { step: 0, tpl: "", rid: "", photo: false, pid: "", done: false }; } };

export default function Onboarding() {
  const router = useRouter(); const [s, setS] = useState<St | null>(null); const { list: templates } = useTemplates();
  useEffect(() => { setS(load()); }, []);
  const upd = (p: Partial<St>) => setS((x) => { const n = { ...(x as St), ...p }; try { localStorage.setItem(KEY, JSON.stringify(n)); } catch {} return n; });
  if (!s) return <Shell title="Welcome" children={<div className="h-40 animate-pulse rounded-2xl bg-white/5" />} />;
  const go = (n: number) => upd({ step: Math.max(0, Math.min(STEPS.length - 1, n)) });
  const finish = () => { upd({ done: true }); toast.success("All set. Your agent is ready."); router.push("/dashboard"); };
  return (
    <Shell title="Let's set up your first agent" subtitle="Five short steps from nothing to a talking AI face. Skip any step and come back later."
      action={<button className="btn-ghost" onClick={() => { upd({ done: true }); router.push("/dashboard"); }}>Skip setup</button>}>
      <div className="mb-8">
        <Progress pct={((s.step + 1) / STEPS.length) * 100} />
        <ol className="mt-3 flex justify-between gap-1 text-xs" aria-label="Setup steps">{STEPS.map((l, i) => (
          <li key={l} className={`flex items-center gap-1.5 ${i === s.step ? "text-white" : i < s.step ? "text-mirage-mint" : "text-gray-600"}`}><span className={`grid h-5 w-5 place-items-center rounded-full text-[10px] ring-1 ring-inset ${i < s.step ? "bg-mirage-mint text-ink ring-mirage-mint" : i === s.step ? "bg-mirage-violet/25 ring-mirage-violet" : "ring-white/15"}`}>{i < s.step ? <Check size={11} /> : i + 1}</span><span className="hidden sm:inline">{l}</span></li>))}</ol>
      </div>
      <AnimatePresence mode="wait">
        <motion.div key={s.step} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }} transition={{ duration: 0.2 }} className="card" data-testid={`step-${s.step + 1}`}>
          {s.step === 0 && <StepTemplate templates={templates} sel={s.tpl} onPick={(id) => { upd({ tpl: id }); go(1); }} onSkip={() => go(1)} />}
          {s.step === 1 && <StepReplica onDone={(r, photo) => { upd({ rid: r.id, photo }); go(2); }} onSkip={() => go(3)} back={() => go(0)} />}
          {s.step === 2 && <StepConsent rid={s.rid} photo={s.photo} onDone={() => go(3)} onSkip={() => go(3)} back={() => go(1)} />}
          {s.step === 3 && <StepPersona tpl={s.tpl} rid={s.rid} onDone={(r) => { upd({ pid: r.persona_id }); }} pid={s.pid} onNext={() => go(4)} onSkip={() => go(4)} back={() => go(2)} />}
          {s.step === 4 && <StepTest pid={s.pid} back={() => go(3)} onFinish={finish} />}
        </motion.div>
      </AnimatePresence>
    </Shell>
  );
}

function Nav({ back, next, nextLabel = "Continue", skip, disabled }: { back?: () => void; next?: () => void; nextLabel?: string; skip?: () => void; disabled?: boolean }) {
  return (
    <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-white/10 pt-4">
      {back && <button type="button" className="btn-ghost" onClick={back}>Back</button>}
      <span className="flex-1" />
      {skip && <button type="button" className="text-sm text-gray-400 underline-offset-2 hover:text-white hover:underline" onClick={skip}>Skip this step</button>}
      {next && <button type="button" className="btn-grad" disabled={disabled} onClick={next}>{nextLabel}<ArrowRight size={15} /></button>}
    </div>
  );
}

function StepTemplate({ templates, sel, onPick, onSkip }: { templates: ReturnType<typeof useTemplates>["list"]; sel: string; onPick: (id: string) => void; onSkip: () => void }) {
  return (<>
    <h2 className="font-display text-3xl">What will your agent do?</h2><p className="mb-5 mt-1 text-sm text-gray-400">Pick a starting point. It comes with a prompt, goals, guardrails and sample knowledge you can edit later.</p>
    {templates === null ? <div className="h-32 animate-pulse rounded-xl bg-white/5" /> : (
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="onb-templates">{templates.map((t) => (
        <li key={t.id}><button type="button" onClick={() => onPick(t.id)} data-template={t.id} className={`flex h-full w-full flex-col rounded-2xl border p-4 text-left transition hover:border-mirage-violet/60 ${sel === t.id ? "border-mirage-violet bg-mirage-violet/10" : "border-white/10 bg-white/[0.03]"}`}>
          <span className="mb-2 w-fit rounded-full bg-mirage-violet/15 px-2 py-0.5 text-[10px] uppercase tracking-wide text-mirage-violet">{t.niche.replace("-", " ")}</span><span className="font-medium">{t.name}</span><span className="mt-1 text-xs leading-relaxed text-gray-400">{t.summary}</span></button></li>))}</ul>)}
    <Nav skip={onSkip} />
  </>);
}

function StepReplica({ onDone, onSkip, back }: { onDone: (r: Replica, photo: boolean) => void; onSkip: () => void; back: () => void }) {
  const [kind, setKind] = useState<"photo" | "video">("photo"); const [name, setName] = useState(""); const [url, setUrl] = useState(""); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try { const r = kind === "photo" ? await api<Replica>("/v1/replicas/photo", { body: { name, photo_url: url } }) : await api<Replica>("/v1/replicas", { body: { name, train_video_url: url } }); if (kind === "photo") markPhotoReplica(r.id); onDone(r, kind === "photo"); }
    catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  return (<>
    <h2 className="font-display text-3xl">Give it a face</h2><p className="mb-5 mt-1 text-sm text-gray-400">A replica is the face and look of your agent. Use a short video of the person, or a single portrait photo.</p>
    <form onSubmit={submit} className="space-y-4">
      <Segmented label="Source" value={kind} onChange={setKind} options={[{ id: "photo", label: <span className="inline-flex items-center gap-1.5"><Camera size={14} />Photo</span> }, { id: "video", label: <span className="inline-flex items-center gap-1.5"><Film size={14} />Video</span> }]} />
      <div className="grid gap-3 sm:grid-cols-2"><Field label="Name"><input className="input" required placeholder="e.g. Founder" value={name} onChange={(e) => setName(e.target.value)} aria-label="Replica name" /></Field>
        <Field label={kind === "photo" ? "Portrait photo URL" : "Training video URL"}><input className="input" type="url" required placeholder="https://..." value={url} onChange={(e) => setUrl(e.target.value)} aria-label="Source URL" /></Field></div>
      {kind === "photo" ? <Callout tone="warn" title="Mouth closed, one face, eyes open">{PHOTO_RULES.map((r) => <span key={r} className="block">- {r}</span>)}</Callout> : <p className="text-xs text-gray-500">A public link to a 1-2 minute video, one face, good light.</p>}
      {err && <Callout tone="bad" title="The server rejected this"><span data-testid="onb-error">{err}</span></Callout>}
      <button className="btn-grad" disabled={busy}>{busy && <Spinner />}Create replica</button>
    </form>
    <Nav back={back} skip={onSkip} />
  </>);
}

function StepConsent({ rid, photo, onDone, onSkip, back }: { rid: string; photo: boolean; onDone: () => void; onSkip: () => void; back: () => void }) {
  const [c, setC] = useState<{ cid: string; phrase: string } | null>(null); const [rep, setRep] = useState<Replica | null>(null); const [consented, setConsented] = useState(false); const [err, setErr] = useState("");
  useEffect(() => { if (!rid) return; const f = () => { api<Replica>(`/v1/replicas/${rid}`).then(setRep).catch(() => {}); api<{ has_consent: boolean }>(`/v1/replicas/${rid}/consent`).then((x) => setConsented(x.has_consent)).catch(() => {}); }; f(); const t = setInterval(f, 5000); return () => clearInterval(t); }, [rid]);
  async function start() { try { const x = await api<{ challenge_id: string; phrase: string }>(`/v1/replicas/${rid}/consent/challenge`, { method: "POST", body: {} }); setC({ cid: x.challenge_id, phrase: x.phrase }); } catch (x) { setErr(errText(x)); } }
  if (!rid) return (<><h2 className="font-display text-3xl">Consent</h2><Callout tone="info">You skipped creating a replica, so there is nothing to consent for yet. You can add one any time under Replicas.</Callout><Nav back={back} next={onDone} /></>);
  return (<>
    <h2 className="font-display text-3xl">Consent comes first</h2>
    <p className="mb-4 mt-1 text-sm text-gray-400">{photo ? "The person in the photo reads a short phrase on camera" : "The person in the video reads a short phrase on camera"}. Mirage checks it is a live person, that the voice or face matches, and only then trains or animates. No consent, no replica.</p>
    {rep && <p className="mb-4 flex items-center gap-2 text-sm text-gray-300"><span className="font-medium">{rep.name}</span><Badge s={consented && rep.status === "awaiting_consent" ? "training" : rep.status} /></p>}
    {consented ? <Callout tone="ok" title="Consent recorded"><span className="flex items-center gap-1.5"><ShieldCheck size={14} />{rep?.status === "ready" ? "The replica is ready." : "Training runs in the background (a worker process); you can carry on while it finishes."}</span></Callout>
      : c ? <ConsentRecorder photo={photo} rid={rid} challengeId={c.cid} phrase={c.phrase} onCancel={() => setC(null)} onDone={() => { setC(null); setConsented(true); }} />
      : <><button type="button" className="btn-grad" onClick={start}><ShieldCheck size={15} />Record consent now</button>{err && <p className="mt-3 text-sm text-mirage-rose" role="alert">{err}</p>}</>}
    <Nav back={back} skip={consented ? undefined : onSkip} next={onDone} nextLabel={consented ? "Continue" : "Continue without consent"} />
  </>);
}

function StepPersona({ tpl, rid, pid, onDone, onNext, onSkip, back }: { tpl: string; rid: string; pid: string; onDone: (r: Instantiated) => void; onNext: () => void; onSkip: () => void; back: () => void }) {
  const [reps, setReps] = useState<Replica[]>([]); const [made, setMade] = useState<Instantiated | null>(null);
  useEffect(() => { api<Replica[]>("/v1/replicas").then(setReps).catch(() => {}); }, []);
  return (<>
    <h2 className="font-display text-3xl">Create the persona</h2><p className="mb-5 mt-1 text-sm text-gray-400">The mind behind the face. Fill in your details; everything else is already written.</p>
    {made || pid ? (<>{made ? <CreatedSummary r={made} /> : <Callout tone="ok" title="Persona already created">Carry on to test it, or open it under Personas to edit.</Callout>}<Nav back={back} next={onNext} /></>)
      : (<><TemplatePicker replicas={reps} defaultReplicaId={rid} initialId={tpl || undefined} onCreated={(r) => { setMade(r); onDone(r); }} compact /><Nav back={back} skip={onSkip} /></>)}
  </>);
}

function StepTest({ pid, back, onFinish }: { pid: string; back: () => void; onFinish: () => void }) {
  const [conv, setConv] = useState<Conversation | null>(null); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  async function start() { setBusy(true); setErr(""); try { const c = await api<Conversation>("/v1/conversations", { body: { persona_id: pid } }); saveId("conversations", c.id); setConv(c); } catch (x) { setErr(errText(x)); } finally { setBusy(false); } }
  return (<>
    <h2 className="font-display text-3xl">Say hello</h2>
    {!pid ? <><p className="mb-4 mt-1 text-sm text-gray-400">No persona yet. Create one under Personas, then talk to it from Conversations.</p><Link href="/dashboard/personas" className="btn">Go to personas</Link></> : (<>
      <p className="mb-4 mt-1 text-sm text-gray-400">Press Start inside the window and allow your microphone. Use headphones so the agent does not hear itself.</p>
      {!conv ? <><button type="button" className="btn-grad" disabled={busy} onClick={start}>{busy ? <Spinner /> : <Play size={15} />}Start test conversation</button>{err && <div className="mt-3"><Callout tone="bad">{err}</Callout></div>}</>
        : <iframe title="Playground" src={`${API_URL}/static/playground.html?cid=${conv.id}&api_key=${encodeURIComponent(getKey())}&api=${encodeURIComponent(API_URL)}`} allow="camera; microphone; autoplay; display-capture" className="h-[560px] w-full rounded-xl border border-white/10 bg-ink" />}
    </>)}
    <Nav back={back} next={onFinish} nextLabel="Finish setup" skip={onFinish} />
    <p className="mt-3 text-xs text-gray-500"><MessagesSquare size={11} className="mr-1 inline" />Finished conversations appear under Conversations with a transcript and summary.</p>
  </>);
}
