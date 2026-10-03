import Shell from "./Shell";
import Reveal from "./Reveal";
import { FinalCTA } from "./Sections";

export const It = ({ children }: { children: React.ReactNode }) => <em className="text-grad pr-1 italic">{children}</em>;

export function PageHead({ eyebrow, title, lead }: { eyebrow: string; title: React.ReactNode; lead?: string }) {
  return (
    <section className="relative overflow-hidden pb-12 pt-14 md:pb-16 md:pt-20">
      <div className="pointer-events-none absolute left-1/2 top-0 h-[360px] w-[760px] -translate-x-1/2 rounded-full bg-mirage-violet/20 blur-[110px]" />
      <Reveal><div className="relative mx-auto w-full max-w-4xl px-5 text-center"><p className="eyebrow">{eyebrow}</p><h1 className="mt-3 font-display text-5xl leading-[1.02] text-white md:text-7xl">{title}</h1>{lead && <p className="mx-auto mt-6 max-w-2xl text-lg text-gray-300">{lead}</p>}</div></Reveal>
    </section>
  );
}
export function Prose({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`mx-auto w-full max-w-3xl px-5 pb-20 [&_h2]:mt-12 [&_h2]:font-display [&_h2]:text-3xl [&_h2]:text-white [&_h3]:mt-6 [&_h3]:font-semibold [&_h3]:text-white [&_li]:mt-2 [&_p]:mt-4 [&_p]:leading-relaxed [&_p]:text-gray-300 [&_ul]:mt-4 [&_ul]:list-disc [&_ul]:pl-6 [&_ul]:text-gray-300 [&_li]:marker:text-mirage-rose [&_a]:text-mirage-rose [&_a]:underline ${className}`}>{children}</div>;
}
export { Shell, FinalCTA };
