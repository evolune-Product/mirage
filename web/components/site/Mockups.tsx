"use client";
import { ReactNode, useEffect, useRef, useState } from "react";
import { useInView, useReducedMotion } from "framer-motion";
import { Check, FileText, ShieldCheck, Upload } from "lucide-react";

export function Window({ title, children, className = "", light = false }: { title: string; children: ReactNode; className?: string; light?: boolean }) {
  return (
    <div className={`overflow-hidden rounded-2xl border shadow-[0_30px_80px_-30px_rgba(79,111,168,.45)] ${light ? "border-black/10 bg-white" : "border-white/10 bg-ink-2"} ${className}`}>
      <div className={`flex items-center gap-2 border-b px-4 py-3 ${light ? "border-black/10 bg-black/[0.03]" : "border-white/10 bg-white/[0.03]"}`}>
        <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" /><span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" /><span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
        <span className="ml-3 truncate font-mono text-[11px] text-gray-400">{title}</span>
      </div>
      {children}
    </div>
  );
}

const script: [("u" | "a"), string][] = [
  ["u", "What does the Pro plan include?"],
  ["a", "Pro is $79 a month with 600 minutes. Past that it is $0.15 per minute."],
  ["u", "Can I run this on my own machine?"],
  ["a", "Yes. The voice stack runs locally for $0 per minute."],
];

export function ConversationMock({ className = "" }: { className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { margin: "-60px" });
  const reduce = useReducedMotion();
  const [n, setN] = useState(reduce ? script.length : 0);
  const [chars, setChars] = useState(0);
  useEffect(() => {
    if (reduce) return;
    if (!inView) return;
    const t = setInterval(() => {
      setChars((c) => {
        if (n >= script.length) { if (c > 40) { setN(0); return 0; } return c + 1; }
        const len = script[n][1].length;
        if (c >= len + 8) { setN((x) => x + 1); return 0; }
        return c + 1;
      });
    }, 32);
    return () => clearInterval(t);
  }, [inView, n, reduce]);
  const shown = reduce ? script : script.slice(0, n);
  const cur = !reduce && n < script.length ? script[n] : null;
  const speaking = cur?.[0] === "a";
  return (
    <div ref={ref} className={className}>
      <Window title="vocalface.com / live conversation">
        <div className="grid md:grid-cols-[1fr_1.1fr]">
          <div className="relative flex min-h-[200px] items-center justify-center bg-[radial-gradient(circle_at_50%_40%,rgba(79,111,168,.35),transparent_65%)] p-6 md:min-h-[360px]">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/images/hero/demo-face.webp" alt="" aria-hidden width={800} height={1000} className={`absolute inset-0 h-full w-full object-cover transition duration-500 ${speaking ? "brightness-105" : "brightness-90"}`} />
            <span className="absolute right-3 top-3 rounded-full bg-black/40 px-2.5 py-1 font-mono text-[10px] text-gray-300">Illustrative image</span>
            <span className="absolute left-3 top-3 flex items-center gap-1.5 rounded-full bg-black/40 px-2.5 py-1 font-mono text-[10px] text-vocalface-mint"><i className="h-1.5 w-1.5 rounded-full bg-vocalface-mint" />{speaking ? "speaking" : cur ? "listening" : "idle"}</span>
            <span className="absolute bottom-3 left-3 rounded-full bg-black/40 px-2.5 py-1 font-mono text-[10px] text-gray-300">first audio 1.4s</span>
          </div>
          <div className="flex min-h-[260px] flex-col justify-end gap-2.5 border-t border-white/10 p-4 md:border-l md:border-t-0">
            {shown.map(([w, t], i) => <Bubble key={i} who={w} text={t} />)}
            {cur && chars > 0 && <Bubble who={cur[0]} text={cur[1].slice(0, chars)} caret />}
          </div>
        </div>
      </Window>
    </div>
  );
}
function Bubble({ who, text, caret }: { who: "u" | "a"; text: string; caret?: boolean }) {
  const u = who === "u";
  return <div className={`max-w-[88%] rounded-2xl px-3.5 py-2 text-[13px] leading-snug ${u ? "self-end bg-white/10 text-gray-100" : "self-start bg-vocalface-violet/25 text-gray-50"}`}>{text}{caret && <span className="ml-0.5 inline-block h-3 w-px translate-y-0.5 animate-pulse bg-white" />}</div>;
}

export function ConsentMock({ className = "" }: { className?: string }) {
  const steps = [["Training video submitted", true], ["Spoken phrase verified", true], ["Replica trained", true], ["Audit log entry written", true]] as const;
  return (
    <Window title="dashboard / replicas / founder" className={className}>
      <div className="space-y-4 p-5">
        <div className="flex items-center justify-between"><div><p className="text-sm font-medium text-white">Founder</p><p className="font-mono text-[11px] text-gray-400">rep_8f21c0</p></div><span className="rounded-full bg-vocalface-mint/15 px-2.5 py-1 font-mono text-[10px] text-vocalface-mint">ready</span></div>
        <div className="rounded-xl border border-white/10 bg-black/30 p-3"><p className="mb-1 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-vocalface-amber"><ShieldCheck size={12} /> Say this phrase aloud</p><p className="text-sm text-gray-200">&ldquo;I, Founder, consent to VocalFace creating a replica of my likeness.&rdquo;</p></div>
        <ul className="space-y-2">{steps.map(([s]) => <li key={s} className="flex items-center gap-2.5 text-[13px] text-gray-300"><span className="grid h-5 w-5 place-items-center rounded-full bg-vocalface-mint/20 text-vocalface-mint"><Check size={12} /></span>{s}</li>)}</ul>
        <div className="h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full w-full rounded-full bg-vocalface-gradient" /></div>
        <div className="flex gap-2"><span className="rounded-full border border-white/15 px-3 py-1.5 text-xs text-gray-300">Revoke consent</span><span className="rounded-full border border-white/15 px-3 py-1.5 text-xs text-gray-300">View audit log</span></div>
      </div>
    </Window>
  );
}

export function KnowledgeMock({ className = "" }: { className?: string }) {
  const docs = [["pricing-faq.md", "12 chunks"], ["onboarding-guide.pdf", "34 chunks"], ["refund-policy.txt", "5 chunks"]];
  return (
    <Window title="dashboard / personas / support-agent / knowledge" className={className} light>
      <div className="space-y-4 bg-[#faf7f1] p-5 text-ink">
        <div className="grid place-items-center rounded-xl border-2 border-dashed border-black/15 py-6 text-center"><Upload size={20} className="text-vocalface-violet" /><p className="mt-2 text-sm font-medium">Drop documents to add knowledge</p><p className="text-xs text-black/60">Indexed for retrieval at answer time</p></div>
        <ul className="space-y-2">{docs.map(([d, c]) => <li key={d} className="flex items-center justify-between rounded-lg border border-black/10 bg-white px-3 py-2 text-[13px]"><span className="flex items-center gap-2"><FileText size={14} className="text-vocalface-rose" />{d}</span><span className="font-mono text-[11px] text-black/60">{c}</span></li>)}</ul>
        <div className="rounded-lg bg-ink p-3 text-[12px] text-gray-300"><span className="font-mono text-vocalface-cyan">retrieved</span> refund-policy.txt, chunk 2 &rarr; used in answer</div>
      </div>
    </Window>
  );
}

export function CodeMock({ className = "" }: { className?: string }) {
  const [tab, setTab] = useState(0);
  const tabs = ["curl", "python", "javascript"];
  const code = [
`curl -X POST http://localhost:8000/v1/conversations \\
  -H "x-api-key: $VOCALFACE_KEY" \\
  -H "content-type: application/json" \\
  -d '{"persona_id": "per_31ab"}'`,
`import requests
r = requests.post(
  "http://localhost:8000/v1/conversations",
  headers={"x-api-key": KEY},
  json={"persona_id": "per_31ab"},
)
print(r.json())`,
`const r = await fetch("http://localhost:8000/v1/conversations", {
  method: "POST",
  headers: { "x-api-key": KEY, "content-type": "application/json" },
  body: JSON.stringify({ persona_id: "per_31ab" }),
});
console.log(await r.json());`];
  return (
    <Window title="POST /v1/conversations" className={className}>
      <div className="flex gap-1 border-b border-white/10 px-3 pt-2">{tabs.map((t, i) => <button key={t} onClick={() => setTab(i)} className={`rounded-t-lg px-3 py-1.5 font-mono text-xs ${tab === i ? "bg-white/10 text-white" : "text-gray-400 hover:text-gray-200"}`}>{t}</button>)}</div>
      <pre className="overflow-x-auto p-4 font-mono text-[12px] leading-relaxed text-gray-200"><code>{code[tab]}</code></pre>
      <div className="border-t border-white/10 bg-black/30 p-4"><p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-vocalface-mint">200 OK</p>
        <pre className="overflow-x-auto font-mono text-[12px] leading-relaxed text-vocalface-cyan"><code>{`{ "id": "conv_77d2",
  "stream": "/v1/conversations/conv_77d2/stream" }`}</code></pre></div>
    </Window>
  );
}

export function LatencyBars() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-60px" });
  const rows: [string, number, string, string][] = [["Speech to text", 0.3, "faster-whisper", "bg-vocalface-cyan"], ["LLM first token", 0.2, "Ollama", "bg-vocalface-violet"], ["Speech synthesis", 0.9, "Kokoro + overhead (remainder)", "bg-vocalface-rose"]];
  return (
    <div ref={ref} className="space-y-4">
      {rows.map(([l, v, s, c]) => (
        <div key={l}><div className="mb-1.5 flex justify-between text-sm"><span className="text-white">{l} <span className="text-gray-400">&middot; {s}</span></span><span className="font-mono text-gray-300">~{v}s</span></div>
          <div className="h-2.5 overflow-hidden rounded-full bg-white/10"><div className={`h-full rounded-full ${c} transition-[width] duration-[1400ms] ease-out`} style={{ width: inView ? `${(v / 1.5) * 100}%` : "0%" }} /></div></div>
      ))}
      <p className="border-t border-white/10 pt-3 font-mono text-xs text-gray-400">Measured locally, end to end: ~1.4s to first audio.</p>
    </div>
  );
}
