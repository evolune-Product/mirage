"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useInView, useReducedMotion } from "framer-motion";
import { ArrowRight, Mic } from "lucide-react";
import LazyOrb from "./LazyOrb";
import type { OrbState } from "./Orb";

const convos = [
  { q: "What does the Pro plan include?", a: "Pro is $79 a month with 600 minutes. Past that it is $0.15 per minute." },
  { q: "Can I self-host the whole thing?", a: "Yes. The voice stack runs on your own machine for $0 per minute. Live GPU face rendering is still on the roadmap." },
  { q: "How does consent work for replicas?", a: "The person on camera speaks a verification phrase first. Consent is revocable and every step lands in an audit log." },
];
const states: [OrbState, string][] = [["idle", "Idle"], ["listening", "Listening"], ["thinking", "Thinking"], ["speaking", "Speaking"]];

export default function TryIt() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { margin: "-80px" });
  const reduce = useReducedMotion();
  const [pick, setPick] = useState(0);
  const [phase, setPhase] = useState<"typing" | "thinking" | "speaking" | "done">("done");
  const [qn, setQn] = useState(convos[0].q.length);
  const [an, setAn] = useState(convos[0].a.length);
  const [manual, setManual] = useState<OrbState | null>(null);
  const run = useRef(0);

  function play(i: number) {
    const id = ++run.current; setPick(i); setManual(null);
    const c = convos[i];
    if (reduce) { setQn(c.q.length); setAn(c.a.length); setPhase("done"); return; }
    setQn(0); setAn(0); setPhase("typing");
    let n = 0;
    const t1 = setInterval(() => { if (run.current !== id) return clearInterval(t1); n++; setQn(n); if (n >= c.q.length) { clearInterval(t1); setPhase("thinking");
      setTimeout(() => { if (run.current !== id) return; setPhase("speaking"); let m = 0;
        const t2 = setInterval(() => { if (run.current !== id) return clearInterval(t2); m += 2; setAn(Math.min(m, c.a.length)); if (m >= c.a.length) { clearInterval(t2); setPhase("done"); } }, 28); }, 900); } }, 38);
  }
  const started = useRef(false);
  useEffect(() => { if (inView && !started.current) { started.current = true; play(0); } }); // eslint-disable-line
  const orb: OrbState = manual ?? (phase === "typing" ? "listening" : phase === "thinking" ? "thinking" : phase === "speaking" ? "speaking" : "idle");
  const c = convos[pick];
  return (
    <section id="try" className="relative overflow-hidden bg-ink-2 py-20 md:py-32">
      <div className="mx-auto w-full max-w-7xl px-5">
        <div className="mx-auto max-w-3xl text-center"><p className="eyebrow">Try it</p>
          <h2 className="mt-3 font-display text-4xl leading-[1.05] text-white sm:text-5xl md:text-6xl">Meet the agent&apos;s <em className="text-grad pr-1 italic">presence</em></h2>
          <p className="mt-5 text-gray-300 md:text-lg">The orb is how VocalFace shows what an agent is doing. Pick a question, or poke the states yourself.</p></div>
        <div ref={ref} className="mt-12 grid items-center gap-8 lg:grid-cols-2 [&>*]:min-w-0">
          <div className="glass relative rounded-3xl p-4">
            <LazyOrb state={orb} level={orb === "speaking" ? 0.45 : orb === "listening" ? 0.18 : 0} className="mx-auto h-[300px] w-full md:h-[380px]" />
            <div role="group" aria-label="Orb state" className="mt-2 flex flex-wrap justify-center gap-2">
              {states.map(([s, l]) => <button key={s} aria-pressed={orb === s} onClick={() => setManual(s)} className={`rounded-full border px-4 py-1.5 text-sm transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-vocalface-cyan ${orb === s ? "border-transparent bg-vocalface-gradient text-ink" : "border-white/15 text-gray-300 hover:bg-white/5"}`}>{l}</button>)}</div>
          </div>
          <div>
            <p className="mb-3 font-mono text-xs uppercase tracking-widest text-gray-400">Ask something</p>
            <div className="flex flex-wrap gap-2">{convos.map((x, i) => <button key={x.q} onClick={() => play(i)} className={`rounded-full border px-4 py-2 text-left text-sm transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-vocalface-cyan ${pick === i ? "border-vocalface-rose/60 bg-vocalface-rose/10 text-white" : "border-white/15 text-gray-300 hover:bg-white/5"}`}>{x.q}</button>)}</div>
            <div className="mt-5 min-h-[230px] space-y-3 rounded-3xl border border-white/10 bg-ink p-5" aria-live="polite">
              {qn > 0 && <div className="ml-auto max-w-[88%] rounded-2xl bg-white/10 px-4 py-2.5 text-sm text-gray-100">{c.q.slice(0, qn)}{phase === "typing" && <span className="ml-0.5 inline-block h-3.5 w-px translate-y-0.5 animate-pulse bg-white" />}</div>}
              {phase === "thinking" && <div className="max-w-[88%] rounded-2xl bg-vocalface-violet/25 px-4 py-3 text-sm text-gray-300" aria-label="Thinking"><span className="inline-flex gap-1"><i className="h-1.5 w-1.5 animate-pulse rounded-full bg-gray-300" /><i className="h-1.5 w-1.5 animate-pulse rounded-full bg-gray-300 [animation-delay:150ms]" /><i className="h-1.5 w-1.5 animate-pulse rounded-full bg-gray-300 [animation-delay:300ms]" /></span></div>}
              {an > 0 && <div className="max-w-[88%] rounded-2xl bg-vocalface-violet/25 px-4 py-2.5 text-sm text-gray-50">{c.a.slice(0, an)}</div>}
            </div>
            <p className="mt-3 text-xs text-gray-400">Simulated for illustration: no model is running in this box. The real thing runs in your dashboard.</p>
            <Link href="/signup" className="btn-grad mt-6 px-7 py-3.5 text-base"><Mic size={16} /> Talk to a demo <ArrowRight size={16} /></Link>
          </div>
        </div>
      </div>
    </section>
  );
}
