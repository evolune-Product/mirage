import Nav from "@/components/site/Nav";
import { Compare, Developers, FAQ, FinalCTA, Features, Footer, Hero, Marquee, Pipeline, Pricing, Roadmap, Trust, UseCases } from "@/components/site/Sections";

export default function Home() {
  return (
    <div className="overflow-x-clip bg-ink">
      <Nav />
      <main>
        <Hero /><Marquee /><Features /><Pipeline /><UseCases /><Trust /><Developers /><Compare /><Pricing /><Roadmap /><FAQ /><FinalCTA />
      </main>
      <Footer />
    </div>
  );
}
