import Shell from "@/components/site/Shell";
import { FinalCTA, UseCases } from "@/components/site/Sections";

export const metadata = { title: "Use cases - VocalFace", description: "Support, sales, training, education, healthcare intake and kiosks.", alternates: { canonical: "/use-cases" } };
export default function Page() {
  return (<Shell><div className="pt-10 md:pt-16 bg-cream"><div className="mx-auto max-w-7xl px-5"><h1 className="font-display text-5xl text-ink md:text-7xl">Where VocalFace <em className="text-grad pr-1 italic">fits</em></h1><p className="mt-5 max-w-2xl text-lg text-black/70">Six places a talking interface beats a text box. Each page shows what the setup looks like and what it does not do.</p></div></div><UseCases /><FinalCTA /></Shell>);
}
