import Link from "next/link";
import { ArrowRight, BookOpen, Check, Cpu, Database, Headphones, Minus, ShieldCheck, Sparkles, Terminal, Video, Globe, GraduationCap, LifeBuoy, Briefcase, X, ChevronDown, Code2, Server, Gauge } from "lucide-react";
import LazyOrb from "./LazyOrb";
import { TrademarkNote } from "./Partners";
import Logo from "./Logo";
import Reveal from "./Reveal";
import { CodeMock, ConsentMock, ConversationMock, KnowledgeMock, LatencyBars } from "./Mockups";
import HowItWorks from "./HowItWorks";
import { useCases } from "@/lib/usecases";

const wrap = "mx-auto w-full max-w-7xl px-5";
const H2 = ({ children, light }: { children: React.ReactNode; light?: boolean }) => <h2 className={`font-display text-4xl leading-[1.05] sm:text-5xl md:text-6xl ${light ? "text-ink" : "text-white"}`}>{children}</h2>;
const It = ({ children }: { children: React.ReactNode }) => <em className="text-grad pr-1 italic">{children}</em>;

export function Hero() {
  return (
    <section className="relative overflow-hidden bg-ink pb-20 pt-12 md:pb-28 md:pt-20">
      <div className="pointer-events-none absolute inset-0 bg-grid-faint [background-size:56px_56px] [mask-image:radial-gradient(ellipse_at_50%_30%,black,transparent_70%)]" />
      <div className="pointer-events-none absolute left-1/2 top-0 h-[520px] w-[900px] -translate-x-1/2 rounded-full bg-vocalface-violet/20 blur-[120px]" />
      <div className="pointer-events-none absolute left-1/2 top-[40px] hidden h-[820px] w-[820px] -translate-x-1/2 opacity-70 md:block"><LazyOrb autoCycle className="h-full w-full" /></div>
      <div className={`${wrap} relative text-center`}>
        <Reveal><span className="glass inline-flex items-center gap-2 rounded-full px-4 py-1.5 font-mono text-xs text-gray-300"><Sparkles size={12} className="text-vocalface-amber" /> Open-core conversational video AI</span></Reveal>
        <Reveal delay={0.08}><h1 className="mx-auto mt-6 max-w-4xl font-display text-[2.9rem] leading-[1] text-white sm:text-6xl md:text-7xl lg:text-[5.5rem]">Talk to an AI that <It>answers</It> in about a second.</h1></Reveal>
        <Reveal delay={0.16}><p className="mx-auto mt-6 max-w-2xl text-base text-gray-300 md:text-lg">VocalFace is the open-core platform for conversational video agents, consent-based replicas and generated video. Use the API, or self-host the whole voice stack for $0 per minute.</p></Reveal>
        <Reveal delay={0.24}><div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row"><Link href="/signup" className="btn-grad px-7 py-3.5 text-base">Talk to a demo <ArrowRight size={16} /></Link><Link href="/#try" className="btn-ghost px-7 py-3.5 text-base">Try it below</Link></div><p className="mt-4 text-xs text-gray-400">Free plan includes 10 minutes. No card needed.</p></Reveal>
        <LazyOrb autoCycle density={1.6} className="mx-auto -mt-2 h-[380px] w-[120%] max-w-none -translate-x-[8%] sm:h-[460px] md:hidden" />
        <div className="glass absolute left-0 top-[26%] hidden animate-float rounded-xl px-4 py-3 text-left xl:block"><p className="font-mono text-[10px] uppercase text-gray-400">time to first audio</p><p className="font-display text-3xl text-white">~1.4s</p></div>
        <div className="glass absolute right-0 top-[38%] hidden animate-float rounded-xl px-4 py-3 text-left [animation-delay:-2s] xl:block"><p className="font-mono text-[10px] uppercase text-gray-400">voice stack, self-hosted</p><p className="font-display text-3xl text-white">$0<span className="text-base text-gray-400">/min</span></p></div>
        <div className="glass absolute left-[3%] top-[42%] hidden animate-float rounded-xl px-4 py-3 text-left [animation-delay:-4s] xl:block"><p className="flex items-center gap-1.5 font-mono text-[10px] uppercase text-vocalface-mint"><ShieldCheck size={12} /> consent verified</p><p className="text-sm text-gray-200">Revocable, audit-logged</p></div>
        <div className="h-8 md:h-24" />
        <Reveal y={50}><ConversationMock className="mx-auto -mt-4 max-w-4xl text-left md:-mt-10" /></Reveal>
      </div>
    </section>
  );
}

export function BrandBanner() {
  return (
    <section aria-label="VocalFace" className="bg-ink px-5 py-14 md:py-20">
      <Reveal>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/brand/hero-logo.webp" width={1600} height={900} alt="VocalFace: AI faces. Real conversations." loading="lazy" className="mx-auto w-full max-w-4xl rounded-3xl border border-white/10" />
      </Reveal>
    </section>
  );
}

export function Marquee() {
  const items = ["Self-hostable", "REST API", "Python SDK", "JavaScript SDK", "Embed widget", "WebSocket streaming", "Consent-gated replicas", "Moderation built in", "Knowledge retrieval", "Open-core"];
  return (
    <section aria-label="Capabilities" className="overflow-hidden border-y border-white/10 bg-ink-2 py-5">
      <div className="flex w-max animate-marquee gap-12 whitespace-nowrap font-mono text-sm uppercase tracking-widest text-gray-400">
        {[...items, ...items].map((t, i) => <span key={i} className="flex items-center gap-12">{t}<span className="text-vocalface-rose">&#10022;</span></span>)}
      </div>
    </section>
  );
}

function Feature({ eyebrow, title, body, bullets, mock, flip, light }: { eyebrow: string; title: React.ReactNode; body: string; bullets: string[]; mock: React.ReactNode; flip?: boolean; light?: boolean }) {
  return (
    <div className={`grid items-center gap-10 md:gap-16 lg:grid-cols-2 [&>*]:min-w-0 ${flip ? "lg:[&>*:first-child]:order-2" : ""}`}>
      <Reveal>
        <p className={`eyebrow ${light ? "!text-[#c2410c]" : ""}`}>{eyebrow}</p>
        <div className="mt-3"><H2 light={light}>{title}</H2></div>
        <p className={`mt-5 max-w-xl text-base md:text-lg ${light ? "text-black/70" : "text-gray-300"}`}>{body}</p>
        <ul className="mt-6 space-y-3">{bullets.map((b) => <li key={b} className={`flex gap-3 text-sm md:text-base ${light ? "text-black/80" : "text-gray-200"}`}><Check size={18} className="mt-0.5 shrink-0 text-vocalface-rose" />{b}</li>)}</ul>
      </Reveal>
      <Reveal delay={0.1} y={40}>{mock}</Reveal>
    </div>
  );
}

export function Features() {
  return (
    <div id="product">
      <section className="bg-ink py-20 md:py-32"><div className={wrap}>
        <Feature eyebrow="Conversations" title={<>Real-time dialogue, <It>streamed</It> end to end</>} body="Speech goes in over a WebSocket, a persona answers, and audio streams back as soon as the first sentence is ready. Turn-taking and transcripts come with it."
          bullets={["About 1.4s to first audio, measured on a local stack", "Live transcript with user and agent turns", "Moderation runs on the conversation"]} mock={<ConversationMock />} />
      </div></section>
      <section className="bg-cream py-20 text-ink md:py-32"><div className={wrap}>
        <Feature light flip eyebrow="Replicas" title={<>Digital twins that need a <It>yes</It> first</>} body="Create a replica from a training video URL. Nothing trains until the person on camera speaks a verification phrase. Consent can be revoked at any time, and every step is written to an audit log."
          bullets={["Spoken phrase verification before training", "Revocable consent with an audit trail", "Lip-sync is approximate today and improving"]} mock={<ConsentMock />} />
      </div></section>
      <section className="bg-ink py-20 md:py-32"><div className={wrap}>
        <Feature eyebrow="Knowledge" title={<>Answers grounded in <It>your</It> documents</>} body="Attach text and documents to a persona. VocalFace chunks and indexes them, then retrieves the relevant passages while the agent is answering."
          bullets={["Upload docs, FAQs and policies", "Retrieval at answer time, not stuffed prompts", "System prompt, voice and replica in one reusable persona"]} mock={<KnowledgeMock />} />
      </div></section>
      <section className="bg-cream py-20 text-ink md:py-32"><div className={wrap}>
        <Feature light flip eyebrow="Video generation" title={<>Script in, <It>video</It> out</>} body="Offline generation is a single REST call: pick a replica, send a script, poll for the finished file. It is a queue-based API, so it fits batch jobs and pipelines."
          bullets={["POST /v1/videos with replica_id and script", "Runs through background workers", "Live real-time face rendering needs an NVIDIA GPU worker and is coming soon"]}
          mock={<div className="rounded-2xl bg-ink p-6 text-white shadow-2xl"><p className="eyebrow">POST /v1/videos</p><pre className="mt-3 overflow-x-auto font-mono text-[12px] leading-relaxed text-gray-200"><code>{`{ "replica_id": "rep_8f21c0",
  "script": "Welcome to the team." }`}</code></pre>
            <div className="mt-5 space-y-2">{[["queued", "w-full bg-white/30"], ["rendering", "w-3/4 bg-vocalface-violet"], ["done", "w-1/2 bg-vocalface-mint"]].map(([s, c]) => <div key={s} className="flex items-center gap-3 font-mono text-[11px] text-gray-400"><span className="w-20">{s}</span><span className={`h-2 rounded-full ${c}`} /></div>)}</div></div>} />
      </div></section>
    </div>
  );
}

export function Pipeline() {
  const steps = [[Headphones, "Listen", "faster-whisper", "~0.3s", "text-vocalface-cyan"], [Cpu, "Think", "Ollama LLM", "~0.2s first token", "text-vocalface-violet"], [Database, "Recall", "Knowledge retrieval", "per answer", "text-vocalface-amber"], [Video, "Speak", "Kokoro TTS", "streams audio", "text-vocalface-rose"]] as const;
  return (
    <section id="how" className="relative overflow-hidden bg-ink-2 py-20 md:py-32">
      <div className={wrap}>
        <Reveal><div className="mx-auto max-w-3xl text-center"><p className="eyebrow">The pipeline</p><div className="mt-3"><H2>Models working <It>in sync</It></H2></div><p className="mt-5 text-gray-300 md:text-lg">Four stages hand off in a stream, so the agent starts speaking before the full answer exists. Every stage is swappable.</p></div></Reveal>
        <div className="mt-14 grid gap-4 md:grid-cols-4">
          {steps.map(([Icon, t, s, m, c], i) => (
            <Reveal key={t} delay={i * 0.08}><div className="card relative h-full"><Icon className={c} size={26} /><p className="mt-5 font-display text-3xl text-white">{t}</p><p className="mt-1 text-sm text-gray-300">{s}</p><p className="mt-4 font-mono text-xs text-gray-400">{m}</p>{i < 3 && <ArrowRight className="absolute -right-3 top-1/2 z-10 hidden -translate-y-1/2 text-vocalface-rose md:block" size={18} />}</div></Reveal>
          ))}
        </div>
        <div className="mt-12 grid items-center gap-10 lg:grid-cols-2 [&>*]:min-w-0">
          <Reveal><div className="card !p-6 md:!p-8"><p className="eyebrow mb-5">Latency, measured locally</p><LatencyBars /></div></Reveal>
          <Reveal delay={0.1}><div className="space-y-4 text-gray-300"><p className="font-display text-3xl text-white md:text-4xl">Fast enough to feel like a <It>conversation</It>.</p><p>These numbers come from our own local stack, not a benchmark lab. Your results depend on your hardware and models. The point is that the whole voice path can run on a single machine with no per-minute vendor bill.</p></div></Reveal>
        </div>
        <HowItWorks />
      </div>
    </section>
  );
}

export function UseCases() {
  return (
    <section className="bg-cream py-20 text-ink md:py-32"><div className={wrap}>
      <Reveal><div className="max-w-3xl"><p className="eyebrow !text-[#c2410c]">Use cases</p><div className="mt-3"><H2 light>Built for the places people <It>talk</It> to software</H2></div></div></Reveal>
      <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {useCases.map((u, i) => (
          <Reveal key={u.slug} delay={(i % 3) * 0.08}><Link href={`/use-cases/${u.slug}`} className="group block h-full rounded-3xl border border-black/10 bg-white p-7 transition duration-300 hover:-translate-y-1.5 hover:shadow-[0_24px_60px_-24px_rgba(124,92,255,.5)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-vocalface-violet">
            <span className="grid h-12 w-12 place-items-center rounded-2xl bg-vocalface-gradient text-white"><u.Icon size={22} /></span><h3 className="mt-6 font-display text-3xl">{u.name}</h3><p className="mt-2 text-black/70">{u.short}</p>
            <p className="mt-5 flex items-center gap-1 text-sm font-medium text-black/70 transition group-hover:text-[#c2185b]">See how <ArrowRight size={14} className="transition group-hover:translate-x-1" /></p></Link></Reveal>
        ))}
      </div>
    </div></section>
  );
}

export function Trust() {
  const items = [[ShieldCheck, "Consent first", "Spoken verification phrase, revocable at any time, with an audit log."], [Sparkles, "Moderation", "Content checks run on scripts and conversations."], [Server, "Self-host", "Run the stack on your own machines. Your data stays yours."], [Gauge, "Honest limits", "Lip-sync is approximate today. Live face rendering needs GPU workers."]] as const;
  return (
    <section className="bg-ink py-20 md:py-28"><div className={wrap}>
      <Reveal><div className="max-w-3xl"><p className="eyebrow">Safety and honesty</p><div className="mt-3"><H2>Likeness is personal. <It>Treat it that way.</It></H2></div></div></Reveal>
      <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{items.map(([I, t, d], i) => <Reveal key={t} delay={i * 0.07}><div className="card h-full transition hover:border-vocalface-rose/50"><I className="text-vocalface-amber" size={24} /><h3 className="mt-4 text-lg font-medium text-white">{t}</h3><p className="mt-2 text-sm text-gray-300">{d}</p></div></Reveal>)}</div>
    </div></section>
  );
}

export function Developers() {
  const pts = [[Terminal, "REST API", "Replicas, personas, conversations, videos."], [Code2, "Python and JS SDKs", "Thin clients over the same API."], [Globe, "Embed widget", "Put an agent on a page with one script."], [Server, "WebSocket stream", "/v1/conversations/{id}/stream"]] as const;
  return (
    <section id="developers" className="bg-ink-2 py-20 md:py-32"><div className={`${wrap} grid items-center gap-12 lg:grid-cols-2 [&>*]:min-w-0`}>
      <Reveal><p className="eyebrow">For developers</p><div className="mt-3"><H2>Four calls from nothing to a <It>talking agent</It></H2></div>
        <p className="mt-5 text-gray-300 md:text-lg">Authenticate with an x-api-key header. Create a replica, a persona, then start a conversation and connect to its stream.</p>
        <ol className="mt-6 space-y-2 font-mono text-sm text-gray-200">{["POST /v1/replicas", "POST /v1/personas", "POST /v1/conversations", "WS   /v1/conversations/{id}/stream?api_key="].map((s, i) => <li key={s} className="flex gap-3"><span className="text-vocalface-rose">{i + 1}</span><span className="break-all">{s}</span></li>)}</ol>
        <div className="mt-8 grid gap-3 sm:grid-cols-2">{pts.map(([I, t, d]) => <div key={t} className="flex gap-3 rounded-xl border border-white/10 p-4"><I size={20} className="mt-0.5 shrink-0 text-vocalface-cyan" /><div><p className="text-sm font-medium text-white">{t}</p><p className="text-xs text-gray-400">{d}</p></div></div>)}</div>
        <div className="mt-8 flex flex-wrap gap-3"><Link href="/dashboard/keys" className="btn-grad">Get an API key <ArrowRight size={16} /></Link><Link href="/docs" className="btn-ghost">Read the docs</Link></div></Reveal>
      <Reveal delay={0.1} y={40}><CodeMock /></Reveal>
    </div></section>
  );
}

const rows: [string, string, string][] = [["Open-core, self-hostable", "yes", "rarely"], ["Voice stack cost when self-hosted", "$0 per minute", "per-minute fees"], ["Swap STT, LLM and TTS", "yes", "limited"], ["Consent-gated replicas", "yes", "varies"], ["Real-time live face rendering", "coming soon (GPU workers)", "yes"], ["Lip-sync polish", "approximate today", "more mature"], ["Own your data", "yes, when self-hosted", "hosted only"]];
export function Compare() {
  return (
    <section className="bg-cream py-20 text-ink md:py-32"><div className="mx-auto w-full max-w-4xl px-5">
      <Reveal><div className="text-center"><p className="eyebrow !text-[#c2410c]">Comparison</p><div className="mt-3"><H2 light>VocalFace vs typical <It>hosted</It> platforms</H2></div></div></Reveal>
      <Reveal delay={0.1}><div className="mt-12 overflow-hidden rounded-3xl border border-black/10 bg-white shadow-xl"><div className="overflow-x-auto"><table className="w-full min-w-[560px] text-left text-sm">
        <thead><tr className="bg-ink text-white"><th className="p-4 font-medium">&nbsp;</th><th className="p-4 font-display text-xl">VocalFace</th><th className="p-4 font-medium text-gray-300">Typical hosted avatar platforms</th></tr></thead>
        <tbody>{rows.map(([a, b, c]) => <tr key={a} className="border-t border-black/10"><td className="p-4 font-medium">{a}</td><td className="p-4"><span className="flex items-center gap-2 font-medium text-ink">{b.startsWith("yes") || b.startsWith("$0") ? <Check size={16} className="text-emerald-600" /> : <Minus size={16} className="text-amber-600" />}{b}</span></td><td className="p-4 text-black/70">{c}</td></tr>)}</tbody></table></div></div>
      <p className="mt-4 text-xs text-black/60">The right-hand column is a general characterization, not a statement about any one vendor. Competitor features and pricing change; please verify current claims with each vendor. We list real-time face rendering and lip-sync as areas where hosted platforms are ahead today.</p></Reveal>
    </div></section>
  );
}

const plans = [
  { n: "Free", p: "$0", per: "", d: "10 minutes to try it.", l: ["10 minutes included", "Full API access", "Community support"], cta: "Start free", href: "/signup" },
  { n: "Starter", p: "$19", per: "/mo", d: "For side projects and pilots.", l: ["120 minutes per month", "$0.20 per minute overage", "Replicas, personas, videos"], cta: "Choose Starter", href: "/signup" },
  { n: "Pro", p: "$79", per: "/mo", d: "For products in production.", l: ["600 minutes per month", "$0.15 per minute overage", "Everything in Starter"], cta: "Choose Pro", href: "/signup", hot: true },
  { n: "Self-host", p: "Free", per: "", d: "Run the whole stack yourself.", l: ["Open-core, full API", "$0 per minute voice stack", "Bring your own hardware"], cta: "Read the docs", href: "/docs/deploy" },
];
export function Pricing({ light = false }: { light?: boolean }) {
  return (
    <section id="pricing" className={`py-20 md:py-32 ${light ? "bg-ink" : "bg-ink"}`}><div className={wrap}>
      <Reveal><div className="mx-auto max-w-3xl text-center"><p className="eyebrow">Pricing</p><div className="mt-3"><H2>Pay for minutes, <It>not mystery</It></H2></div><p className="mt-5 text-gray-300 md:text-lg">Start free. Upgrade when you ship. Or self-host and pay nothing to us.</p></div></Reveal>
      <div className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {plans.map((p, i) => (
          <Reveal key={p.n} delay={i * 0.07} className="h-full"><div className={`relative flex h-full flex-col rounded-3xl border ${p.hot ? "border-transparent bg-vocalface-gradient p-[1px]" : "border-white/10 bg-ink-2 p-7"}`}>
            <div className={`flex h-full flex-col ${p.hot ? "rounded-[23px] bg-ink-2 p-7" : ""}`}>
              {p.hot && <span className="absolute -top-3 left-7 rounded-full bg-vocalface-gradient px-3 py-1 text-[11px] font-semibold text-white">Most popular</span>}
              <h3 className="font-display text-3xl text-white">{p.n}</h3><p className="mt-3"><span className="font-display text-5xl text-white">{p.p}</span><span className="text-gray-400">{p.per}</span></p><p className="mt-2 text-sm text-gray-300">{p.d}</p>
              <ul className="mt-6 flex-1 space-y-3">{p.l.map((x) => <li key={x} className="flex gap-2.5 text-sm text-gray-200"><Check size={16} className="mt-0.5 shrink-0 text-vocalface-mint" />{x}</li>)}</ul>
              <Link href={p.href} className={`${p.hot ? "btn-grad" : "btn-ghost"} mt-8`}>{p.cta}</Link></div></div></Reveal>
        ))}
      </div>
      <Reveal><div className="card mt-8 !p-6"><p className="font-medium text-white">Need more minutes? Buy a top-up any time.</p>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">{[["$12", "60 min"], ["$50", "300 min"], ["$150", "1,000 min"]].map(([a, b]) => <div key={b} className="flex items-baseline justify-between rounded-xl border border-white/10 px-4 py-3"><span className="font-display text-3xl text-white">{a}</span><span className="font-mono text-sm text-gray-300">{b}</span></div>)}</div></div></Reveal>
    </div></section>
  );
}

const faqs = [
  ["Does live, real-time face video ship today?", "Not yet. Today you get real-time voice conversations and offline video generation. Live real-time face rendering needs an NVIDIA GPU worker and is coming soon."],
  ["How accurate is the lip-sync?", "Approximate today. We would rather say that plainly than overpromise. It is an active area of work."],
  ["Is it really $0 per minute?", "When self-hosted, the voice stack (faster-whisper, an Ollama LLM and Kokoro TTS) runs on your own hardware, so there is no per-minute fee to us or to model vendors. You still pay for your own compute."],
  ["How does consent work?", "A replica only trains after the person on camera speaks a verification phrase. Consent is revocable, and each step is recorded in an audit log."],
  ["Can I use my own models?", "The STT, LLM and TTS stages are separate, so they can be swapped. Local models work out of the box."],
  ["What does overage cost?", "Starter is $0.20 per minute past 120 minutes. Pro is $0.15 per minute past 600. Top-ups are $12 for 60 minutes, $50 for 300 and $150 for 1,000."],
  ["Is the 1.4s figure a guarantee?", "No. It is what we measured locally for time to first audio. Your hardware and model choices change it."],
];
export function FAQ() {
  return (
    <section id="faq" className="bg-cream py-20 text-ink md:py-32"><div className="mx-auto w-full max-w-3xl px-5">
      <Reveal><div className="text-center"><p className="eyebrow !text-[#c2410c]">FAQ</p><div className="mt-3"><H2 light>Straight <It>answers</It></H2></div></div></Reveal>
      <div className="mt-12 space-y-3">{faqs.map(([q, a]) => (
        <Reveal key={q}><details className="group rounded-2xl border border-black/10 bg-white px-6 py-5 open:shadow-lg"><summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-left font-medium [&::-webkit-details-marker]:hidden">{q}<ChevronDown size={18} className="shrink-0 transition group-open:rotate-180" /></summary><p className="mt-3 text-black/70">{a}</p></details></Reveal>))}</div>
    </div></section>
  );
}

export function Roadmap() {
  const cols = [["Ships today", "text-vocalface-mint", ["REST API, Python and JS SDKs", "Real-time voice conversations", "Consent-gated replicas", "Knowledge retrieval", "Offline video generation", "Embed widget, self-hosting"]], ["Coming soon", "text-vocalface-amber", ["Live real-time face rendering on NVIDIA GPU workers", "Tighter lip-sync", "More voices and languages"]]] as const;
  return (
    <section id="roadmap" className="bg-ink pb-20 md:pb-28"><div className="mx-auto w-full max-w-5xl px-5">
      <Reveal><div className="text-center"><p className="eyebrow">Roadmap</p><div className="mt-3"><H2>What ships, and what <It>doesn&apos;t yet</It></H2></div></div></Reveal>
      <div className="mt-12 grid gap-5 md:grid-cols-2">{cols.map(([t, c, l]) => <Reveal key={t}><div className="card h-full !p-7"><h3 className={`font-display text-3xl ${c}`}>{t}</h3><ul className="mt-5 space-y-3">{l.map((x) => <li key={x} className="flex gap-3 text-gray-200">{t === "Ships today" ? <Check size={18} className="mt-0.5 shrink-0 text-vocalface-mint" /> : <Sparkles size={18} className="mt-0.5 shrink-0 text-vocalface-amber" />}{x}</li>)}</ul></div></Reveal>)}</div>
    </div></section>
  );
}

export function FinalCTA() {
  return (
    <section className="relative overflow-hidden bg-ink py-24 md:py-36">
      <div className={`${wrap} relative text-center`}>
        <LazyOrb autoCycle density={0.8} className="mx-auto mb-4 h-[260px] w-full max-w-xl md:h-[340px]" />
        <Reveal><h2 className="mx-auto max-w-3xl font-display text-5xl leading-[1] text-white md:text-7xl">Give your product a <It>face</It> and a voice.</h2>
          <p className="mx-auto mt-6 max-w-xl text-gray-200 md:text-lg">Start on the free plan or clone the repo and run it yourself.</p>
          <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row"><Link href="/signup" className="btn-grad px-8 py-4 text-base">Talk to a demo <ArrowRight size={16} /></Link><Link href="/docs" className="btn-ghost px-8 py-4 text-base backdrop-blur">Read the docs</Link></div></Reveal>
      </div>
    </section>
  );
}

export function Footer() {
  const cols = [
    ["Product", [["Conversations", "/#product"], ["Replicas", "/#product"], ["Video generation", "/#product"], ["Compare", "/compare"], ["Roadmap", "/#roadmap"]]],
    ["Use cases", useCases.map((u) => [u.name, `/use-cases/${u.slug}`])],
    ["Developers", [["Docs", "/docs"], ["API reference", "/docs/api"], ["Self-host", "/docs/deploy"], ["API keys", "/dashboard/keys"], ["Changelog", "/changelog"]]],
    ["Company", [["Pricing", "/pricing"], ["About", "/about"], ["Pitch", "/pitch"], ["Security", "/security"], ["Terms", "/terms"], ["Privacy", "/privacy"]]],
  ] as const;
  return (
    <footer className="border-t border-white/10 bg-ink-2 pb-10 pt-16"><div className={wrap}>
      <div className="grid gap-10 sm:grid-cols-2 lg:grid-cols-[1.4fr_repeat(4,1fr)]">
        <div><Link href="/" aria-label="VocalFace home"><Logo className="text-white" /></Link><p className="mt-3 max-w-xs text-sm text-gray-400">Open-core conversational video AI. Self-host it or use the API.</p><p className="mt-2 max-w-xs text-xs text-gray-500">A product of Evolune EdgeTech LLP.</p></div>
        {cols.map(([t, l]) => <nav key={t} aria-label={t}><p className="font-mono text-xs uppercase tracking-widest text-gray-400">{t}</p><ul className="mt-4 space-y-2.5">{l.map(([a, h]) => <li key={a}><Link href={h} className="text-sm text-gray-300 hover:text-white">{a}</Link></li>)}</ul></nav>)}
      </div>
      <div className="mt-14 flex flex-col justify-between gap-3 border-t border-white/10 pt-6 text-xs text-gray-400 sm:flex-row"><div className="space-y-1"><p>&copy; 2026 Evolune EdgeTech LLP. VocalFace is a product of Evolune EdgeTech LLP. All rights reserved.</p><TrademarkNote /></div><p>Replicas require the explicit, revocable consent of the person depicted.</p></div>
    </div></footer>
  );
}
