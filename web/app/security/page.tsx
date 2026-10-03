import { Check, FileSearch, Server, ShieldCheck, Trash2, UserCheck, Info } from "lucide-react";
import { FinalCTA, It, PageHead, Shell } from "@/components/site/Page";
import Reveal from "@/components/site/Reveal";

export const metadata = { title: "Security and consent - Mirage", description: "Consent-first replicas, moderation, audit log, data deletion and self-hosting, with the current limits stated plainly.", alternates: { canonical: "/security" } };

const items = [
  [UserCheck, "Consent-first replicas", "A replica cannot be marked ready without a consent record. The person on camera records their voice reading a one-time phrase with a random code. The server transcribes it, checks the code words, and compares the speaker's voice with the voice in the training video. Challenges expire after 15 minutes and work once, and consent can be revoked at any time."],
  [ShieldCheck, "Moderation", "Scripts and conversations are checked against a built-in blocklist. Add your own terms, or point Mirage at a local Ollama model for an extra classifier. If the classifier is down it falls back to the blocklist."],
  [FileSearch, "Audit log", "Sensitive account actions are written to an audit log you can read through GET /v1/audit and the dashboard."],
  [Trash2, "Deletion", "Knowledge documents and consent records can be deleted through the API. When you self-host, you own the database and can delete anything. For account-wide deletion on the hosted service, contact us."],
  [Server, "Self-hosting", "Run the API, models and storage on your own hardware so audio, transcripts and likenesses never leave your network. The voice stack needs no third-party model vendor."],
] as const;

export default function Page() {
  return (
    <Shell>
      <PageHead eyebrow="Security and consent" title={<>Likeness is personal. <It>Treat it that way.</It></>} lead="What Mirage does today to keep replicas consensual and data under your control, and where the gaps still are." />
      <div className="mx-auto grid w-full max-w-5xl gap-5 px-5 md:grid-cols-2">
        {items.map(([I, t, d], i) => <Reveal key={t} delay={(i % 2) * 0.08}><div className="card h-full !p-7"><I className="text-mirage-amber" size={26} /><h2 className="mt-4 font-display text-3xl text-white">{t}</h2><p className="mt-3 text-gray-300">{d}</p></div></Reveal>)}
      </div>
      <div className="mx-auto mt-12 w-full max-w-5xl px-5 pb-20"><div className="rounded-3xl border border-mirage-amber/30 bg-mirage-amber/5 p-7">
        <h2 className="flex items-center gap-2 font-display text-3xl text-white"><Info size={22} className="text-mirage-amber" /> Known limits</h2>
        <ul className="mt-4 space-y-3 text-gray-200">
          {["Consent checks the spoken phrase and that the voice matches the training video, but it is not liveness detection: a live voice clone could pass it, and the face is not tied to the voice.", "Rate limiting is in-process, so it applies per API worker.", "We have no SOC 2, ISO 27001 or HIPAA certification. Do not assume them.", "Billing webhooks are tested with signatures, not against live provider endpoints."].map((x) => <li key={x} className="flex gap-3"><Check size={18} className="mt-0.5 shrink-0 text-mirage-amber" />{x}</li>)}
        </ul>
        <p className="mt-5 text-sm text-gray-300">Found a vulnerability? Please report it responsibly to the maintainers listed in the repository.</p>
      </div></div>
      <FinalCTA />
    </Shell>
  );
}
