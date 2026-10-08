import Shell from "@/components/site/Shell";
import TryIt from "@/components/site/TryIt";
import { Backing } from "@/components/site/Partners";
import { BrandBanner, Compare, Developers, FAQ, FinalCTA, Features, Hero, Marquee, Pipeline, Pricing, Roadmap, Trust, UseCases } from "@/components/site/Sections";

export const metadata = { alternates: { canonical: "/" } };
export default function Home() {
  return (
    <Shell>
      <Hero /><Marquee /><Backing /><BrandBanner /><TryIt /><Features /><Pipeline /><UseCases /><Trust /><Developers /><Compare /><Pricing /><Roadmap /><FAQ /><FinalCTA />
    </Shell>
  );
}
