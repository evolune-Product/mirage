import Link from "next/link";
const feats = [
  ["Conversational video", "Real-time face-to-face agents with turn-taking, STT, LLM and TTS streamed end to end."],
  ["Replicas", "Create a digital twin from a single training video URL."],
  ["Personas & knowledge", "System prompt, knowledge text, replica and voice in one reusable persona."],
  ["Video generation", "Script plus replica in, rendered video out, via a simple REST call."],
];
const rows = [["Pricing model","Per-second metering, pay for what you use","Plan tiers plus overage"],["Open source core","Yes","No"],["Self-host","Yes, bring your own GPU","No"],["Swap LLM / TTS / STT","Any provider, incl. free local models","Limited"],["Starting price","$0 self-hosted, 600 free seconds on cloud","Paid plan required"]];
const plans = [["Self-host","Free","Run the whole stack yourself. Bring your own GPU.",["Open-core, full API","Any LLM / TTS provider","Community support"]],["Cloud Starter","Pay as you go","Hosted by us, metered per second.",["600 free seconds on signup","Replicas, personas, videos","Email support"]],["Scale","Custom","Volume pricing and dedicated GPUs.",["Volume discounts","SLA","Private deployment help"]]] as const;
export default function Home() {
  return (<div>
    <header className="mx-auto flex max-w-6xl items-center justify-between p-6"><span className="text-lg font-semibold">Mirage</span>
      <nav className="flex items-center gap-4 text-sm text-gray-300"><a href="#pricing">Pricing</a><Link href="/dashboard">Dashboard</Link><Link className="btn" href="/signup">Get API key</Link></nav></header>
    <section className="mx-auto max-w-4xl px-6 py-20 text-center">
      <p className="mb-4 text-sm text-accent">The open-core alternative to Tavus</p>
      <h1 className="text-4xl font-bold leading-tight md:text-6xl">Conversational video AI that costs less and runs anywhere</h1>
      <p className="mx-auto mt-6 max-w-2xl text-lg text-gray-400">Build real-time AI agents with a face. Use our cloud, or self-host the whole thing on your own GPUs.</p>
      <div className="mt-8 flex justify-center gap-3"><Link className="btn" href="/signup">Start free</Link><Link className="btn-ghost" href="/dashboard/keys">Read the docs</Link></div>
    </section>
    <section className="mx-auto grid max-w-6xl gap-4 px-6 md:grid-cols-4">{feats.map(([t, d]) => <div key={t} className="card"><h3 className="font-medium">{t}</h3><p className="mt-2 text-sm text-gray-400">{d}</p></div>)}</section>
    <section className="mx-auto max-w-4xl px-6 py-20"><h2 className="mb-6 text-2xl font-semibold">Mirage vs Tavus</h2>
      <div className="overflow-x-auto"><table className="card w-full text-left text-sm"><thead className="text-gray-400"><tr><th className="p-3"></th><th className="p-3 text-accent">Mirage</th><th className="p-3">Tavus</th></tr></thead>
      <tbody>{rows.map(r => <tr key={r[0]} className="border-t border-line">{r.map((c, i) => <td key={i} className="p-3">{c}</td>)}</tr>)}</tbody></table></div>
      <p className="mt-3 text-xs text-gray-500">Comparison reflects our understanding of public information; verify current vendor pricing and features.</p></section>
    <section id="pricing" className="mx-auto max-w-6xl px-6 pb-24"><h2 className="mb-6 text-2xl font-semibold">Pricing</h2>
      <div className="grid gap-4 md:grid-cols-3">{plans.map(([n, p, d, l]) => <div key={n} className="card"><h3 className="font-medium">{n}</h3><p className="my-2 text-2xl font-bold">{p}</p><p className="text-sm text-gray-400">{d}</p><ul className="mt-4 space-y-1 text-sm text-gray-300">{l.map(x => <li key={x}>- {x}</li>)}</ul></div>)}</div>
      <p className="mt-3 text-xs text-gray-500">Placeholder pricing; final rates are not set yet.</p></section>
  </div>);
}
