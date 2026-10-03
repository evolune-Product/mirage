import { ReactNode } from "react";

export const slugify = (s: string) => s.toLowerCase().replace(/`/g, "").replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");

export function inline(s: string, key = ""): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)]+\))/g;
  let last = 0, m: RegExpExecArray | null, i = 0;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    const t = m[0];
    if (t[0] === "`") out.push(<code key={key + i} className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[0.85em] text-mirage-cyan [overflow-wrap:anywhere]">{t.slice(1, -1)}</code>);
    else if (t[0] === "*") out.push(<strong key={key + i} className="font-semibold text-white">{t.slice(2, -2)}</strong>);
    else { const mm = /\[([^\]]+)\]\(([^)]+)\)/.exec(t)!; out.push(<a key={key + i} href={mm[2]} className="text-mirage-rose underline underline-offset-2 hover:text-white">{mm[1]}</a>); }
    last = m.index + t.length; i++;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

export type Section = { id: string; title: string; text: string };

/** Split markdown into h2 sections (for search). */
export function sections(md: string): Section[] {
  const res: Section[] = []; let cur: Section | null = null;
  for (const line of md.split("\n")) {
    const h = /^#{1,3} (.+)/.exec(line);
    if (h && line.startsWith("## ")) { cur = { id: slugify(h[1]), title: h[1], text: "" }; res.push(cur); }
    else if (cur) cur.text += line.replace(/[`*]/g, "") + " ";
  }
  return res;
}

export function Markdown({ source }: { source: string }) {
  const lines = source.split("\n"); const out: ReactNode[] = []; let i = 0, k = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    if (l.startsWith("```")) {
      const buf: string[] = []; i++;
      while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]); i++;
      out.push(<pre key={k++} className="my-5 overflow-x-auto rounded-xl border border-white/10 bg-black/40 p-4 font-mono text-[12.5px] leading-relaxed text-gray-200"><code>{buf.join("\n")}</code></pre>); continue;
    }
    const h = /^(#{1,3}) (.+)/.exec(l);
    if (h) {
      const id = slugify(h[2]); const n = h[1].length;
      if (n === 1) out.push(<h1 key={k++} id={id} className="font-display text-5xl text-white md:text-6xl">{h[2]}</h1>);
      else if (n === 2) out.push(<h2 key={k++} id={id} className="mt-14 scroll-mt-24 border-t border-white/10 pt-8 font-display text-3xl text-white md:text-4xl"><a href={`#${id}`} className="hover:text-mirage-amber">{h[2]}</a></h2>);
      else out.push(<h3 key={k++} id={id} className="mt-8 scroll-mt-24 text-lg font-semibold text-white">{h[2]}</h3>);
      i++; continue;
    }
    if (l.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) { if (!/^\|[\s|:-]+\|?$/.test(lines[i])) rows.push(lines[i].replace(/^\||\|$/g, "").split("|").map((c) => c.trim())); i++; }
      out.push(<div key={k++} className="my-5 overflow-x-auto rounded-xl border border-white/10"><table className="w-full min-w-[420px] text-left text-sm"><thead><tr className="bg-white/5">{rows[0].map((c, j) => <th key={j} className="p-3 font-medium text-white">{inline(c)}</th>)}</tr></thead><tbody>{rows.slice(1).map((r, a) => <tr key={a} className="border-t border-white/10">{r.map((c, j) => <td key={j} className="p-3 align-top text-gray-300">{inline(c)}</td>)}</tr>)}</tbody></table></div>); continue;
    }
    if (/^(- |\d+\. )/.test(l)) {
      const ord = /^\d+\. /.test(l); const items: string[] = [];
      while (i < lines.length && /^(- |\d+\. )/.test(lines[i])) items.push(lines[i++].replace(/^(- |\d+\. )/, ""));
      const cls = `my-4 space-y-2 pl-6 text-gray-300 ${ord ? "list-decimal" : "list-disc"} marker:text-mirage-rose`;
      const kids = items.map((t, j) => <li key={j} className="pl-1 leading-relaxed">{inline(t, `l${j}`)}</li>);
      out.push(ord ? <ol key={k++} className={cls}>{kids}</ol> : <ul key={k++} className={cls}>{kids}</ul>); continue;
    }
    const buf: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(```|#|\||- |\d+\. )/.test(lines[i])) buf.push(lines[i++]);
    out.push(<p key={k++} className="my-4 leading-relaxed text-gray-300">{inline(buf.join(" "))}</p>);
  }
  return <>{out}</>;
}
