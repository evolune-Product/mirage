import Link from "next/link";
import Reveal from "@/components/site/Reveal";
import { It, PageHead, Shell } from "@/components/site/Page";
import { Backing, COMPANY, TrademarkNote } from "@/components/site/Partners";

// Unlisted on purpose: this page is frank about gaps, so it is noindex and not in the sitemap. Share the link with investors.
export const metadata = {
  title: "Investor pitch - VocalFace",
  description: "VocalFace: open-core conversational video AI. What is built, what is measured, what is not, and where it goes next.",
  alternates: { canonical: "/pitch" },
  robots: { index: false, follow: false },
};

const contact = process.env.NEXT_PUBLIC_CONTACT_EMAIL;
const shell = "mx-auto w-full max-w-5xl px-5";
const h2 = "font-display text-3xl text-white md:text-4xl";

const proof: [string, string][] = [
  ["447", "backend tests passing, plus a 13-step browser end-to-end suite"],
  ["1.4 to 1.9 s", "from the end of speech to first lip movement, warm, on a MacBook (M1 Pro). A measurement, not a guarantee"],
  ["8", "ready-made agent templates: sales demo, support, tutoring, intake and more"],
  ["0", "customers and $0 revenue. We say so up front"],
];

const pillars: [string, string][] = [
  ["Real-time conversations", "Speech in, answer and a talking face out, over one WebSocket. Turn-taking, interruptions and echo handling are built in."],
  ["Replicas with consent", "A replica from a short video or a single photo, gated by a spoken, revocable consent step with voice and face matching and an audit log."],
  ["Video generation API", "Script in, talking-head video out. Cloned voice, captions, backgrounds, 16:9 / 9:16 / 1:1, multi-scene."],
  ["Platform for builders", "Knowledge from documents and web pages with citations, guardrails, tools, webhooks, analytics, an embeddable widget, guest and scheduled links, SDKs."],
];

const why: [string, string][] = [
  ["Open-core and self-hostable", "Teams that need data control can run the whole stack. Others can use our API. Every stage (speech, model, voice, face) is swappable."],
  ["Consent first", "Likeness without consent is a procurement blocker for serious buyers. We made consent a core feature, not an add-on."],
  ["Cost structure", "The voice path has no per-minute vendor fee when self-hosted. The GPU face is the one variable cost, and we will publish the measured number after our GPU tests."],
  ["A path to our own model", "The platform and the consented data we collect are ours. Our own fine-tuned face model is a funded next step, not a claim today."],
];

const road: [string, string, string][] = [
  ["Next 30 days", "Prove real time", "Test real-time face generation on NVIDIA GPUs and measure latency and cost per user-minute. Replace the two research-licensed legacy components. Legal review of the open-model licences."],
  ["30 to 90 days", "First users", "Hosted backend, then pilots with 5 to 10 design partners in one niche (sales demo agents or multilingual tutoring). Measure retention and willingness to pay."],
  ["3 to 6 months", "Our own model", "Train a face model on consented data. Our estimate: 2 to 4 months and roughly $20k to $60k of GPU for a first usable version. An estimate, not a quote."],
];

const open: [string, string][] = [
  ["Open face-animation model", "face and head motion from audio (Apache-2.0 per its repository)"],
  ["Chatterbox", "voice cloning (MIT)"],
  ["Kokoro", "voices (Apache-2.0)"],
  ["Whisper / faster-whisper", "speech to text (MIT)"],
  ["Silero VAD", "voice activity (MIT)"],
  ["Ollama and open language models", "answers, under each model's own terms"],
];

export default function Page() {
  return (
    <Shell>
      <PageHead eyebrow="Investor pitch" title={<>Conversational video, <It>owned</It> by the teams that use it</>}
        lead={`VocalFace is an open-core platform for real-time face-to-face AI agents and consent-based digital replicas. A product of ${COMPANY}.`} />
      <Backing />

      <section className={`${shell} py-16`}>
        <Reveal><h2 className={h2}>The problem</h2>
          <p className="mt-4 max-w-3xl text-lg leading-relaxed text-gray-300">Hosted avatar platforms are closed, priced per minute, and keep your data on their servers. Teams that want a talking agent with a face either pay a premium for every minute, or stitch five open-source models together themselves and own the integration, safety and consent work. There is no open, consent-first option that a business can run itself.</p></Reveal>
      </section>

      <section className={`${shell} pb-16`}>
        <Reveal><h2 className={h2}>What exists today</h2><p className="mt-3 max-w-3xl text-gray-400">A working prototype built in October 2026. Everything below is measured on our own machines.</p></Reveal>
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {proof.map(([n, t]) => <div key={t} className="card !p-6"><p className="font-display text-4xl text-grad">{n}</p><p className="mt-3 text-sm leading-relaxed text-gray-300">{t}</p></div>)}
        </div>
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          {pillars.map(([a, b]) => <div key={a} className="card !p-6"><h3 className="font-semibold text-white">{a}</h3><p className="mt-2 text-sm leading-relaxed text-gray-300">{b}</p></div>)}
        </div>
      </section>

      <section className={`${shell} pb-16`}>
        <Reveal><h2 className={h2}>How we chose the face model</h2>
          <p className="mt-4 max-w-3xl leading-relaxed text-gray-300">We tried seven open approaches for the face, including lip-sync models, audio-driven talking-head models, a small video-generation model and a prototype we trained ourselves. We kept the one that preserved identity best and moved the whole face most naturally from audio alone. That was one rater&apos;s judgement on one face, so independent testing is on the plan. It is designed to run in real time on a single modern NVIDIA GPU; we have not yet measured that ourselves.</p></Reveal>
      </section>

      <section className={`${shell} pb-16`}>
        <Reveal><h2 className={h2}>Why this can win</h2><p className="mt-3 text-gray-400">These are hypotheses we are testing, not facts.</p></Reveal>
        <div className="mt-8 grid gap-4 md:grid-cols-2">
          {why.map(([a, b]) => <div key={a} className="card !p-6"><h3 className="font-semibold text-white">{a}</h3><p className="mt-2 text-sm leading-relaxed text-gray-300">{b}</p></div>)}
        </div>
        <p className="mt-6 max-w-3xl text-sm text-gray-400">The category is funded and moving fast: for example, Captions rebranded as Mirage and has reported more than $100M raised ({" "}
          <a className="underline" href="https://techcrunch.com/2025/09/04/captions-rebrands-as-mirage-expands-beyond-creator-tools-to-ai-video-research" target="_blank" rel="noreferrer">TechCrunch</a>). Hosted leaders are ahead on live face quality today; see <Link className="underline" href="/compare">our honest comparison</Link>.</p>
      </section>

      <section className={`${shell} pb-16`}>
        <Reveal><h2 className={h2}>Roadmap and use of funds</h2></Reveal>
        <div className="mt-8 grid gap-4 md:grid-cols-3">
          {road.map(([when, t, d]) => <div key={when} className="card !p-6"><p className="eyebrow">{when}</p><h3 className="mt-2 font-display text-2xl text-white">{t}</h3><p className="mt-3 text-sm leading-relaxed text-gray-300">{d}</p></div>)}
        </div>
        <p className="mt-6 max-w-3xl text-sm text-gray-400">Funds would go to GPU infrastructure, one machine-learning engineer, legal review, and design-partner pilots. Pricing is usage-based with a free tier (see <Link className="underline" href="/pricing">pricing</Link>). Unit economics are not measured yet and will be published after the GPU tests.</p>
      </section>

      <section className={`${shell} pb-16`}>
        <Reveal><h2 className={h2}>Where we are, honestly</h2></Reveal>
        <div className="mt-8 grid gap-4 md:grid-cols-2">
          <div className="card !p-6"><h3 className="font-semibold text-white">Done</h3><ul className="mt-3 space-y-2 text-sm text-gray-300"><li>Working real-time voice agent and generated-video pipeline</li><li>Consent, security and moderation layers, tested</li><li>Dashboard, API, SDKs and embeddable widget</li><li>Member of the NVIDIA Inception program and Anthropic&apos;s Claude for Startups program</li></ul></div>
          <div className="card !p-6"><h3 className="font-semibold text-white">Not yet</h3><ul className="mt-3 space-y-2 text-sm text-gray-300"><li>No customers or revenue</li><li>Real-time face quality is below the best hosted platforms today</li><li>Two legacy research-licensed components (a live lip-sync model and a photo-animation fallback) must be replaced before commercial launch</li><li>Not yet tested: phones, hosted deployment, live payments</li></ul></div>
        </div>
      </section>

      <section className={`${shell} pb-16`}>
        <Reveal><h2 className={h2}>Built on open source</h2><p className="mt-3 max-w-3xl text-gray-400">We credit what we build on and keep every licence notice. Licences are summarised from each project&apos;s own repository; we will have counsel review them before commercial launch.</p></Reveal>
        <ul className="mt-6 grid gap-3 sm:grid-cols-2">{open.map(([a, b]) => <li key={a} className="rounded-2xl border border-white/10 bg-ink-2 p-4 text-sm"><span className="font-semibold text-white">{a}</span> <span className="text-gray-400">- {b}</span></li>)}</ul>
      </section>

      <section className={`${shell} pb-24`}>
        <div className="card !p-8 text-center">
          <h2 className={h2}>Talk to us</h2>
          <p className="mx-auto mt-3 max-w-xl text-gray-300">We are looking for design partners, GPU and infrastructure support, and early investors who care about open, consent-first AI video.</p>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
            {contact ? <a className="btn-grad" href={`mailto:${contact}?subject=VocalFace`}>Email us</a> : <Link className="btn-grad" href="/signup">Try the product</Link>}
            <Link className="btn-ghost" href="/docs">Read the docs</Link>
          </div>
          <p className="mt-6 text-xs text-gray-500">&copy; 2026 {COMPANY}. This page describes a pre-revenue prototype. Statements about the future are plans and estimates, not promises.</p>
          <div className="mt-2 text-xs text-gray-500"><TrademarkNote /></div>
        </div>
      </section>
    </Shell>
  );
}
