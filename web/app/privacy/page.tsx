import { PageHead, Prose, Shell } from "@/components/site/Page";
import { Info } from "lucide-react";

export const metadata = { title: "Privacy Policy (template) - VocalFace", description: "Draft template, not legal advice.", alternates: { canonical: "/privacy" }, robots: { index: false } };
export default function Page() {
  return (
    <Shell>
      <PageHead eyebrow="Legal draft" title="Privacy Policy" />
      <div className="mx-auto mb-8 flex w-full max-w-3xl gap-3 px-5"><div className="flex gap-3 rounded-2xl border border-vocalface-amber/40 bg-vocalface-amber/10 p-4 text-sm text-gray-100"><Info size={18} className="mt-0.5 shrink-0 text-vocalface-amber" /><p><strong>Template: have counsel review.</strong> This is a draft starting point, not legal advice, and is not yet in force. Bracketed items are placeholders.</p></div></div>
      <Prose><h2>What we collect</h2><p>Account email, API usage and ledger records, and content you submit: training videos, consent audio and transcripts, knowledge documents, scripts and conversation transcripts.</p><h2>How we use it</h2><p>To run the service, meter usage, enforce consent and moderation, and keep an audit log. [Confirm any analytics or subprocessors before publishing.]</p><h2>Biometric and likeness data</h2><p>Replica source media is processed only after consent is recorded, and can be removed when consent is revoked. [Counsel to review biometric-data laws that apply to your users.]</p><h2>Retention and deletion</h2><p>[Retention periods to be defined.] Knowledge documents and consent records can be deleted via the API. Contact us for account-wide deletion.</p><h2>Self-hosting</h2><p>If you self-host VocalFace, you are the data controller and none of this data is sent to us.</p><h2>Contact</h2><p>[Contact address to be added.]</p></Prose>
    </Shell>
  );
}
