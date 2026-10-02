import Nav from "@/components/site/Nav";
import { FAQ, FinalCTA, Footer, Pricing } from "@/components/site/Sections";

export const metadata = { title: "Pricing - Mirage" };
export default function PricingPage() {
  return (<div className="overflow-x-clip bg-ink"><Nav /><main><Pricing /><FAQ /><FinalCTA /></main><Footer /></div>);
}
