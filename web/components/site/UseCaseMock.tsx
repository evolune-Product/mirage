import { Window } from "./Mockups";
import type { UseCase } from "@/lib/usecases";

export default function UseCaseMock({ u }: { u: UseCase }) {
  return (
    <Window title={u.window}>
      <div className="grid md:grid-cols-[0.8fr_1.2fr]">
        <div className="relative flex min-h-[180px] items-center justify-center bg-[radial-gradient(circle_at_50%_40%,rgba(79,111,168,.35),transparent_65%)] p-6">
          <svg viewBox="0 0 120 120" className="h-28 w-28 md:h-36 md:w-36" aria-hidden><defs><linearGradient id={`g-${u.slug}`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#7dd3fc" /><stop offset=".5" stopColor="#bcd4ea" /><stop offset="1" stopColor="#4f6fa8" /></linearGradient></defs>
            <circle cx="60" cy="60" r="44" fill={`url(#g-${u.slug})`} /><circle cx="60" cy="60" r="54" fill="none" stroke="#fff" strokeOpacity=".15" /><circle cx="60" cy="60" r="58" fill="none" stroke="#fff" strokeOpacity=".07" /></svg>
          <span className="absolute left-3 top-3 rounded-full bg-black/40 px-2.5 py-1 font-mono text-[10px] text-vocalface-mint">illustrative</span>
        </div>
        <div className="flex flex-col justify-end gap-2.5 border-t border-white/10 p-4 md:border-l md:border-t-0">
          {u.script.map(([w, t], i) => <div key={i} className={`max-w-[88%] rounded-2xl px-3.5 py-2 text-[13px] leading-snug ${w === "u" ? "self-end bg-white/10 text-gray-100" : "self-start bg-vocalface-violet/25 text-gray-50"}`}>{t}</div>)}
          <div className="mt-2 flex flex-wrap gap-2">{u.chips.map((c) => <span key={c} className="rounded-full border border-white/15 px-2.5 py-1 font-mono text-[10px] text-gray-300">{c}</span>)}</div>
        </div>
      </div>
    </Window>
  );
}
