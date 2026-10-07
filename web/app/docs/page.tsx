import Link from "next/link";
import { ArrowRight } from "lucide-react";
import Shell from "@/components/site/Shell";
import DocsShell from "@/components/site/DocsShell";
import { docPages, searchIndex } from "@/lib/docs";

export const metadata = { title: "Docs - VocalFace", description: "API reference and self-hosting guide for VocalFace.", alternates: { canonical: "/docs" } };

const quick = `# 1. sign up (600 free seconds)
curl -X POST http://localhost:8000/v1/signup -H "content-type: application/json" -d '{"email":"you@example.com"}'

# 2. create a persona
curl -X POST http://localhost:8000/v1/personas -H "x-api-key: $VOCALFACE_KEY" \\
  -H "content-type: application/json" -d '{"name":"Support","system_prompt":"You are a friendly support agent."}'

# 3. start a conversation, then open the WebSocket it returns
curl -X POST http://localhost:8000/v1/conversations -H "x-api-key: $VOCALFACE_KEY" \\
  -H "content-type: application/json" -d '{"persona_id":"<id>"}'`;

export default function DocsHome() {
  return (
    <Shell>
      <DocsShell pages={docPages} index={searchIndex()} active="overview" toc={[]}>
        <p className="eyebrow">Documentation</p>
        <h1 className="mt-3 font-display text-5xl text-white md:text-6xl">Build with VocalFace</h1>
        <p className="mt-5 text-lg text-gray-300">Everything here is rendered from the same markdown files that live in the repository, so the docs and the code ship together. Authenticate with an <code className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-sm text-vocalface-cyan">x-api-key</code> header.</p>
        <h2 className="mt-12 font-display text-3xl text-white">Quickstart</h2>
        <p className="mt-3 text-gray-300">Replace the host with your own deployment. Replicas need a consent step before they can be used; see the API reference.</p>
        <pre className="my-5 overflow-x-auto rounded-xl border border-white/10 bg-black/40 p-4 font-mono text-[12.5px] leading-relaxed text-gray-200"><code>{quick}</code></pre>
        <div className="mt-10 grid gap-4 sm:grid-cols-2">{docPages.map((p) => (
          <Link key={p.slug} href={`/docs/${p.slug}`} className="card group transition hover:border-vocalface-rose/50"><h3 className="font-display text-2xl text-white">{p.title}</h3><p className="mt-2 text-sm text-gray-300">{p.blurb}</p><p className="mt-4 flex items-center gap-1 text-sm text-vocalface-rose">Read <ArrowRight size={14} className="transition group-hover:translate-x-1" /></p></Link>))}</div>
      </DocsShell>
    </Shell>
  );
}
