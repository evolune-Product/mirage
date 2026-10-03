import fs from "node:fs";
import path from "node:path";
import { sections, Section } from "@/components/site/md";

export const docPages = [
  { slug: "api", title: "API reference", blurb: "Every route: accounts, replicas, consent, personas, conversations, videos, billing, moderation." },
  { slug: "deploy", title: "Self-hosting", blurb: "Run the API, Ollama, workers and optional GPU box on your own machines." },
  { slug: "benchmarks", title: "Benchmarks", blurb: "Raw numbers from our local stack, including what did not work." },
  { slug: "testing", title: "Testing", blurb: "How the platform is tested end to end." },
] as const;

export function loadDoc(slug: string) { return fs.readFileSync(path.join(process.cwd(), "content", "docs", `${slug}.md`), "utf8"); }
export function searchIndex(): (Section & { slug: string; page: string })[] {
  return docPages.flatMap((p) => sections(loadDoc(p.slug)).map((s) => ({ ...s, slug: p.slug, page: p.title })));
}
