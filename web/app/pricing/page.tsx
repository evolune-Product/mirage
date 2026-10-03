import Shell from "@/components/site/Shell";
import { FAQ, FinalCTA, Pricing } from "@/components/site/Sections";

export const metadata = { title: "Pricing - Mirage", description: "Free, Starter, Pro and self-host. Pay for minutes, not mystery.", alternates: { canonical: "/pricing" } };
export default function PricingPage() {
  return (<Shell><h1 className="sr-only">Pricing</h1><Pricing /><FAQ /><FinalCTA /></Shell>);
}
