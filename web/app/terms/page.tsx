import { PageHead, Prose, Shell } from "@/components/site/Page";
import { Info } from "lucide-react";

export const metadata = { title: "Terms of Service (template) - Mirage", description: "Draft template, not legal advice.", alternates: { canonical: "/terms" }, robots: { index: false } };
export default function Page() {
  return (
    <Shell>
      <PageHead eyebrow="Legal draft" title="Terms of Service" />
      <div className="mx-auto mb-8 flex w-full max-w-3xl gap-3 px-5"><div className="flex gap-3 rounded-2xl border border-mirage-amber/40 bg-mirage-amber/10 p-4 text-sm text-gray-100"><Info size={18} className="mt-0.5 shrink-0 text-mirage-amber" /><p><strong>Template: have counsel review.</strong> This is a draft starting point, not legal advice, and is not yet in force. Bracketed items are placeholders.</p></div></div>
      <Prose><h2>1. Using Mirage</h2><p>You may use the service and API in line with these terms and applicable law. You are responsible for activity under your API keys.</p><h2>2. Likeness and consent</h2><p>You may only create a replica of a person who has given explicit consent, verified through the consent flow. You must not use Mirage to impersonate, deceive or harm anyone, and you must honor revocations.</p><h2>3. Acceptable use</h2><p>No unlawful, harassing, deceptive or sexually exploitative content. We may suspend accounts that violate this section.</p><h2>4. Billing</h2><p>Paid plans and top-ups are billed as described on the pricing page. Credits are consumed per second of conversation.</p><h2>5. Availability and warranty</h2><p>The service is provided as is, without warranty. Features marked coming soon are not commitments.</p><h2>6. Liability</h2><p>[Limitation of liability clause to be drafted by counsel.]</p><h2>7. Changes and contact</h2><p>[Governing law, change process and contact details to be added.]</p></Prose>
    </Shell>
  );
}
