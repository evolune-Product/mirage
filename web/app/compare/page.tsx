import { Check, Minus, Clock } from "lucide-react";
import Link from "next/link";
import { FinalCTA, It, PageHead, Shell } from "@/components/site/Page";

export const metadata = { title: "Mirage vs hosted platforms - Compare", description: "An honest comparison of Mirage with hosted conversational video platforms, including where they are ahead.", alternates: { canonical: "/compare" } };

const rows: [string, string, string, "y" | "n" | "w"][] = [
  ["Open-core and self-hostable", "Yes. Run the whole stack yourself.", "Typically hosted only.", "y"],
  ["Voice stack cost when self-hosted", "$0 per minute to us. You pay for your own compute.", "Typically billed per minute.", "y"],
  ["Swap STT, LLM and TTS", "Yes. Each stage is a provider interface.", "Varies; often a fixed stack.", "y"],
  ["Consent-gated replicas", "Yes, with a spoken one-time phrase and audit log.", "Varies by vendor.", "y"],
  ["Real-time face quality", "Wav2Lip-class lip-sync at small sizes. Live NVIDIA-GPU quality is coming.", "More mature today. This is where hosted platforms lead.", "w"],
  ["Time to first audio", "About 1.4s on our local stack. Not a guarantee.", "Some advertise sub-second. Verify with your own tests.", "w"],
  ["Operations burden", "You run it, unless you use our hosted API.", "They run it for you.", "w"],
  ["Track record, scale and certifications", "New project. No certifications. No customer list to show.", "Established vendors often have all three.", "w"],
];
export default function Page() {
  return (
    <Shell>
      <PageHead eyebrow="Compare" title={<>Mirage vs <It>hosted</It> platforms</>} lead="We would rather you choose well than choose us. Here is where each approach wins." />
      <div className="mx-auto w-full max-w-5xl px-5">
        <div className="overflow-hidden rounded-3xl border border-white/10 bg-ink-2"><div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm">
          <caption className="sr-only">Comparison of Mirage and typical hosted platforms</caption>
          <thead><tr className="bg-white/5"><th scope="col" className="p-4 font-medium text-gray-300">&nbsp;</th><th scope="col" className="p-4 font-display text-xl text-white">Mirage</th><th scope="col" className="p-4 font-medium text-gray-300">Typical hosted platforms</th></tr></thead>
          <tbody>{rows.map(([a, b, c, k]) => <tr key={a} className="border-t border-white/10 align-top"><th scope="row" className="p-4 font-medium text-white">{a}</th><td className="p-4 text-gray-200"><span className="flex gap-2">{k === "y" ? <Check size={16} className="mt-0.5 shrink-0 text-mirage-mint" /> : <Minus size={16} className="mt-0.5 shrink-0 text-mirage-amber" />}{b}</span></td><td className="p-4 text-gray-300">{c}</td></tr>)}</tbody>
        </table></div></div>
        <p className="mt-4 flex gap-2 text-xs text-gray-400"><Clock size={14} className="mt-0.5 shrink-0" />The right-hand column is a general characterization, not a statement about any single vendor. Features and pricing change often; please verify current claims directly with each vendor before deciding.</p>
        <div className="mt-12 grid gap-5 md:grid-cols-2 pb-20">
          <div className="card !p-7"><h2 className="font-display text-3xl text-white">Choose Mirage if</h2><ul className="mt-4 space-y-2 text-gray-300"><li>You need to own data and infrastructure.</li><li>Per-minute costs will dominate your bill.</li><li>You want to swap models and providers.</li><li>Voice-first, with an approximate face, is good enough for now.</li></ul></div>
          <div className="card !p-7"><h2 className="font-display text-3xl text-white">Choose a hosted platform if</h2><ul className="mt-4 space-y-2 text-gray-300"><li>You need the best live face quality today.</li><li>You do not want to operate anything.</li><li>You need enterprise certifications now.</li><li>You want a proven track record at scale.</li></ul><p className="mt-4 text-sm text-gray-400">See <Link href="/#roadmap" className="text-mirage-rose underline">what ships today</Link>.</p></div>
        </div>
      </div>
      <FinalCTA />
    </Shell>
  );
}
