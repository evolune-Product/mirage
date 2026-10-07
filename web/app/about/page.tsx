import Link from "next/link";
import { FinalCTA, It, PageHead, Prose, Shell } from "@/components/site/Page";

export const metadata = { title: "About - VocalFace", description: "Why VocalFace exists, and what is real today.", alternates: { canonical: "/about" } };
export default function Page() {
  return (
    <Shell>
      <PageHead eyebrow="About" title={<>Conversational video you can <It>own</It></>} lead="VocalFace is an open-core platform for face-to-face AI agents, built to run on commodity hardware first." />
      <Prose>
        <h2>Why we are building it</h2>
        <p>Talking to software should feel like talking to someone. The hosted platforms that do this well charge per minute and keep everything on their servers. We think the voice and orchestration layers can be open, swappable and cheap, with a hosted option for people who would rather not run anything.</p>
        <h2>What is real today</h2>
        <ul><li>Real-time voice conversations over a WebSocket, about 1.4 seconds to first audio on our local stack.</li><li>Consent-gated replicas, personas, knowledge retrieval and offline video generation through one REST API.</li><li>A live lip-synced face using a Wav2Lip-class model, real time on Apple Silicon at small sizes. It is not yet the quality of the best hosted systems.</li></ul>
        <h2>What is not</h2>
        <ul><li>Live, high-fidelity face rendering needs an NVIDIA GPU worker. It is coming, not shipped.</li><li>We do not list customers, logos or user counts because we have none to show yet.</li></ul>
        <h2>Principles</h2>
        <ul><li>Consent before likeness. Always.</li><li>Say what works and what does not.</li><li>Keep every stage swappable.</li></ul>
        <p>Read the <Link href="/changelog">changelog</Link> to see how fast it moves, or the <Link href="/docs">docs</Link> to try it.</p>
      </Prose>
      <FinalCTA />
    </Shell>
  );
}
