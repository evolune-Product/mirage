import { FinalCTA, It, PageHead, Shell } from "@/components/site/Page";
import { commits } from "@/lib/commits";

export const metadata = { title: "Changelog - VocalFace", description: "What changed and when, taken from the project's git history.", alternates: { canonical: "/changelog" } };

export default function Page() {
  const byDate = new Map<string, [string, string][]>();
  [...commits].reverse().forEach(([h, d, s]) => byDate.set(d, [...(byDate.get(d) ?? []), [h, s]]));
  return (
    <Shell>
      <PageHead eyebrow="Changelog" title={<>Shipping in <It>public</It></>} lead="Taken straight from the repository's git history, newest first." />
      <ol className="mx-auto w-full max-w-3xl space-y-10 px-5 pb-20">
        {[...byDate].map(([d, list]) => (
          <li key={d} className="grid gap-3 md:grid-cols-[120px_1fr]">
            <time dateTime={d} className="font-mono text-sm text-vocalface-amber">{d}</time>
            <ul className="space-y-3 border-l border-white/10 pl-5">{list.map(([h, s]) => <li key={h} className="relative"><span className="absolute -left-[25px] top-2 h-2 w-2 rounded-full bg-vocalface-rose" /><p className="text-gray-100">{s}</p><p className="font-mono text-xs text-gray-400">{h}</p></li>)}</ul>
          </li>
        ))}
      </ol>
      <FinalCTA />
    </Shell>
  );
}
