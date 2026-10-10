import fs from "fs";
import path from "path";

// The official NVIDIA Inception member badge is issued to members through the Inception member portal and may only be used
// unmodified and as NVIDIA authorises. Drop the file into web/public/partners/ named nvidia-inception.(svg|png|webp|jpg).
// Until it exists we show a plain-text, factual membership line instead of any NVIDIA artwork.
function partnerFile(base: string): string | null {
  for (const ext of ["svg", "png", "webp", "jpg"]) {
    const f = `${base}.${ext}`;
    if (fs.existsSync(path.join(process.cwd(), "public", "partners", f))) return `/partners/${f}`;
  }
  return null;
}
export const inceptionBadge = () => partnerFile("nvidia-inception");
// Same rule for Anthropic's Claude for Startups: use only the official logo file Anthropic provides, per its brand guidelines.
// Drop it in web/public/partners/ as claude-for-startups.(svg|png|webp|jpg).
export const claudeBadge = () => partnerFile("claude-for-startups");

export const COMPANY = "Evolune EdgeTech LLP";

/** Slim "who is behind this" strip for the landing page. */
export function Backing() {
  const badge = inceptionBadge();
  const claude = claudeBadge();
  return (
    <section aria-label="Company and programs" className="border-y border-white/10 bg-ink-2/60">
      <div className="mx-auto flex w-full max-w-7xl flex-col items-center justify-center gap-4 px-5 py-6 text-center sm:flex-row sm:gap-10">
        <p className="text-sm text-gray-300">A product of <span className="font-semibold text-white">{COMPANY}</span></p>
        <span className="hidden h-6 w-px bg-white/15 sm:block" aria-hidden />
        {badge
          ? <div className="flex items-center gap-3"><img src={badge} alt="NVIDIA Inception Program member" className="h-10 w-auto" /><p className="max-w-xs text-left text-xs text-gray-400">Member of the NVIDIA Inception program for startups. Membership does not imply endorsement by NVIDIA.</p></div>
          : <p className="text-sm text-gray-300">Member of the <span className="font-semibold text-white">NVIDIA Inception</span> program for startups <span className="text-xs text-gray-500">(membership does not imply endorsement)</span></p>}
        <span className="hidden h-6 w-px bg-white/15 sm:block" aria-hidden />
        {claude
          ? <div className="flex items-center gap-3"><img src={claude} alt="Claude for Startups" className="h-10 w-auto" /><p className="max-w-xs text-left text-xs text-gray-400">Part of Anthropic&apos;s Claude for Startups program. Participation does not imply endorsement by Anthropic.</p></div>
          : <p className="text-sm text-gray-300">Part of Anthropic&apos;s <span className="font-semibold text-white">Claude for Startups</span> program <span className="text-xs text-gray-500">(participation does not imply endorsement)</span></p>}
      </div>
    </section>
  );
}

export function TrademarkNote() {
  return (
    <>
      {inceptionBadge() ? <p>NVIDIA and the NVIDIA logo are trademarks and/or registered trademarks of NVIDIA Corporation in the U.S. and other countries.</p> : null}
      <p>Claude is a trademark of Anthropic, PBC.</p>
    </>
  );
}
