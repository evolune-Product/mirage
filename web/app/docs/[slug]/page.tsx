import { notFound } from "next/navigation";
import Shell from "@/components/site/Shell";
import DocsShell from "@/components/site/DocsShell";
import { docPages, loadDoc, searchIndex } from "@/lib/docs";
import { Markdown, sections } from "@/components/site/md";

export const dynamicParams = false;
export function generateStaticParams() { return docPages.map((p) => ({ slug: p.slug })); }
export function generateMetadata({ params }: { params: { slug: string } }) {
  const p = docPages.find((d) => d.slug === params.slug);
  return { title: `${p?.title ?? "Docs"} - Mirage docs`, description: p?.blurb, alternates: { canonical: `/docs/${params.slug}` } };
}

export default function DocPage({ params }: { params: { slug: string } }) {
  if (!docPages.some((d) => d.slug === params.slug)) notFound();
  const md = loadDoc(params.slug);
  return (
    <Shell>
      <DocsShell pages={docPages} index={searchIndex()} active={params.slug} toc={sections(md).map((s) => ({ id: s.id, title: s.title }))}>
        <Markdown source={md} />
      </DocsShell>
    </Shell>
  );
}
