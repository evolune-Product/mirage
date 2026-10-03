import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowRight, Check, Info } from "lucide-react";
import Shell from "@/components/site/Shell";
import Reveal from "@/components/site/Reveal";
import UseCaseMock from "@/components/site/UseCaseMock";
import { FinalCTA } from "@/components/site/Sections";
import { findUseCase, useCases } from "@/lib/usecases";

export const dynamicParams = false;
export const generateStaticParams = () => useCases.map((u) => ({ slug: u.slug }));
export function generateMetadata({ params }: { params: { slug: string } }) {
  const u = findUseCase(params.slug); if (!u) return {};
  return { title: `${u.name} - Mirage`, description: u.lead, alternates: { canonical: `/use-cases/${u.slug}` } };
}

export default function UseCasePage({ params }: { params: { slug: string } }) {
  const u = findUseCase(params.slug); if (!u) notFound();
  const others = useCases.filter((x) => x.slug !== u.slug).slice(0, 3);
  return (
    <Shell>
      <section className="relative overflow-hidden pb-16 pt-14 md:pb-24 md:pt-20">
        <div className="pointer-events-none absolute left-1/2 top-0 h-[420px] w-[800px] -translate-x-1/2 rounded-full bg-mirage-violet/20 blur-[120px]" />
        <div className="relative mx-auto grid w-full max-w-7xl items-center gap-12 px-5 lg:grid-cols-2 [&>*]:min-w-0">
          <Reveal>
            <Link href="/use-cases" className="text-sm text-gray-300 hover:text-white">&larr; All use cases</Link>
            <p className="eyebrow mt-6">{u.eyebrow}</p>
            <h1 className="mt-3 font-display text-5xl leading-[1.02] text-white md:text-6xl">{u.headline[0]}<em className="text-grad pr-1 italic">{u.headline[1]}</em>{u.headline[2]}</h1>
            <p className="mt-6 text-lg text-gray-300">{u.lead}</p>
            <div className="mt-8 flex flex-wrap gap-3"><Link href="/signup" className="btn-grad px-7 py-3.5 text-base">Build this free <ArrowRight size={16} /></Link><Link href="/docs" className="btn-ghost px-7 py-3.5 text-base">Read the docs</Link></div>
          </Reveal>
          <Reveal delay={0.1} y={40}><UseCaseMock u={u} /></Reveal>
        </div>
      </section>
      <section className="bg-ink-2 py-16 md:py-24"><div className="mx-auto w-full max-w-7xl px-5">
        <div className="grid gap-4 md:grid-cols-3">{u.points.map(([t, d], i) => <Reveal key={t} delay={i * 0.07}><div className="card h-full"><Check className="text-mirage-mint" size={22} /><h2 className="mt-4 text-lg font-medium text-white">{t}</h2><p className="mt-2 text-sm text-gray-300">{d}</p></div></Reveal>)}</div>
      </div></section>
      <section className="bg-cream py-16 text-ink md:py-24"><div className="mx-auto w-full max-w-5xl px-5">
        <h2 className="font-display text-4xl md:text-5xl">Up and running in three steps</h2>
        <ol className="mt-10 grid gap-5 md:grid-cols-3">{u.steps.map(([t, d], i) => <li key={t} className="rounded-3xl border border-black/10 bg-white p-6"><span className="font-display text-5xl text-grad">{i + 1}</span><h3 className="mt-3 text-lg font-semibold">{t}</h3><p className="mt-2 text-sm text-black/70">{d}</p></li>)}</ol>
        <div className="mt-8 flex gap-3 rounded-2xl border border-[#c2410c]/30 bg-[#fff4ea] p-5 text-sm text-black/80"><Info size={18} className="mt-0.5 shrink-0 text-[#c2410c]" /><p><strong>Honest note.</strong> {u.caution}</p></div>
      </div></section>
      <section className="bg-ink py-16"><div className="mx-auto w-full max-w-7xl px-5">
        <p className="font-mono text-xs uppercase tracking-widest text-gray-400">More use cases</p>
        <div className="mt-5 grid gap-4 md:grid-cols-3">{others.map((o) => <Link key={o.slug} href={`/use-cases/${o.slug}`} className="card group transition hover:border-mirage-rose/50"><o.Icon className="text-mirage-amber" size={22} /><h3 className="mt-3 font-display text-2xl text-white">{o.name}</h3><p className="mt-1 text-sm text-gray-300">{o.short}</p></Link>)}</div>
      </div></section>
      <FinalCTA />
    </Shell>
  );
}
