import { LifeBuoy, Briefcase, GraduationCap, Stethoscope, MonitorSmartphone, BookOpen } from "lucide-react";

export type UseCase = {
  slug: string; name: string; short: string; Icon: typeof LifeBuoy;
  eyebrow: string; headline: [string, string, string]; // before, italic, after
  lead: string; points: [string, string][]; steps: [string, string][];
  script: [("u" | "a"), string][]; window: string; chips: string[]; caution: string;
};

export const useCases: UseCase[] = [
  { slug: "support", name: "Support agents", short: "A face for your help center, grounded in your docs.", Icon: LifeBuoy,
    eyebrow: "Customer support", headline: ["A support rep that has ", "read", " every doc"],
    lead: "Attach your help center, refund policy and onboarding guide to a persona. The agent retrieves the relevant passage while it answers, so replies stay tied to what you actually wrote.",
    points: [["Grounded answers", "Knowledge retrieval runs at answer time instead of stuffing a prompt."], ["Always on", "A persona does not need a shift schedule. Hand off to a human when it should."], ["Your data stays put", "Self-host the stack and tickets never leave your network."]],
    steps: [["Upload docs", "Text, markdown and files go into a persona's knowledge base."], ["Write the persona", "Tone, escalation rules and voice in one system prompt."], ["Embed the widget", "One script tag puts the agent on your help page."]],
    script: [["u", "I was charged twice for last month."], ["a", "Sorry about that. Per the refund policy, duplicate charges are reversed within five business days. I can note it on your account now."]],
    window: "help.acme.example / chat", chips: ["knowledge: refund-policy.txt", "moderation on"], caution: "Wire up your own escalation path. VocalFace does not connect to ticketing systems out of the box." },
  { slug: "sales", name: "Sales and onboarding", short: "Walk new users through your product, on demand.", Icon: Briefcase,
    eyebrow: "Sales and onboarding", headline: ["Every visitor gets a ", "guided", " first call"],
    lead: "Give prospects and new sign-ups a conversational guide that knows your pricing, plans and setup steps, and can talk at 2 a.m. when your team cannot.",
    points: [["Product-aware", "Feed it your pricing page, docs and FAQ."], ["Remembers context", "Persona memories carry what was said across conversations."], ["Metered per minute", "Usage ledger shows exactly what each conversation cost."]],
    steps: [["Create a persona", "Describe the product and the questions it should field."], ["Add knowledge", "Pricing, comparison notes and integration guides."], ["Start conversations", "From the API or the embed widget, metered per second."]],
    script: [["u", "Is there a plan for a team of five?"], ["a", "Pro covers that with 600 minutes a month. Want me to walk through setup?"]],
    window: "acme.example / onboarding", chips: ["memory enabled", "usage metered"], caution: "Pricing shown is an illustrative example persona, not a promise about your numbers." },
  { slug: "training", name: "Interactive training", short: "Role-play scenarios with a persona that remembers context.", Icon: BookOpen,
    eyebrow: "Corporate training", headline: ["Practice the hard ", "conversation", " first"],
    lead: "Build a persona that plays the difficult customer, the nervous new hire or the skeptical buyer, and let people rehearse with real back-and-forth instead of a quiz.",
    points: [["Scenario personas", "A system prompt defines the character and the rules of the exercise."], ["Transcripts", "Review what was said, turn by turn."], ["Consent-based replicas", "Use a real trainer's likeness only with their verified, revocable consent."]],
    steps: [["Script the scenario", "Goals, tone and what a good outcome looks like."], ["Run sessions", "Voice conversations stream over a WebSocket."], ["Review transcripts", "Pull them from the conversations API."]],
    script: [["a", "I have been waiting forty minutes and nobody has helped me."], ["u", "I am sorry about the wait. Let me find out what happened."]],
    window: "training / de-escalation drill", chips: ["role: upset customer", "transcript saved"], caution: "Scoring and analytics are not built in. You get transcripts and the API." },
  { slug: "education", name: "Tutors and coaching", short: "Patient, always-available practice partners.", Icon: GraduationCap,
    eyebrow: "Education", headline: ["A tutor that never ", "runs out", " of patience"],
    lead: "Language practice, interview rehearsal and homework help all share one shape: someone to talk to who answers right away and does not get tired of the same question.",
    points: [["Voice first", "Speech in, speech out, with turn-taking handled."], ["Course material as knowledge", "Ground the tutor in your syllabus."], ["Moderation", "Content checks run on conversations."]],
    steps: [["Write the tutor", "Level, language and teaching style."], ["Attach the syllabus", "Retrieval keeps answers on topic."], ["Open a session", "Learners talk through the embed widget or your app."]],
    script: [["u", "Can you explain photosynthesis again, slower?"], ["a", "Sure. Plants take in light, water and carbon dioxide. Let us start with the light."]],
    window: "learn.example / biology tutor", chips: ["knowledge: syllabus.md", "moderation on"], caution: "If learners are minors, the legal duties (consent, data handling) are yours to assess with counsel." },
  { slug: "healthcare-intake", name: "Healthcare intake", short: "Collect routine intake details before the appointment.", Icon: Stethoscope,
    eyebrow: "Healthcare intake", headline: ["Gather the ", "basics", " before the visit"],
    lead: "A conversational front desk can ask the routine questions, such as reason for visit and preferred times, and hand a transcript to staff. It is a convenience layer for administrative intake only.",
    points: [["Self-hostable", "Keep audio and transcripts on infrastructure you control."], ["Audit log", "Account-level audit rows for sensitive actions."], ["Data deletion", "Delete what you stored when it is no longer needed."]],
    steps: [["Define the script", "Which questions, in what order, and what to never answer."], ["Self-host", "Run API, models and storage inside your environment."], ["Pass transcripts to staff", "Pull them from the API into your own system."]],
    script: [["a", "What brings you in this week?"], ["u", "A follow-up on my knee, preferably mornings."]],
    window: "clinic.example / intake", chips: ["self-hosted", "audit log"], caution: "VocalFace is not certified for HIPAA or any health regulation, and gives no medical advice. Do not use it for diagnosis or triage. Compliance review is your responsibility." },
  { slug: "kiosks", name: "Kiosks and embeds", short: "Drop the widget into any site with a script tag.", Icon: MonitorSmartphone,
    eyebrow: "Kiosks and embeds", headline: ["A host for the ", "front", " of the store"],
    lead: "Lobbies, events and storefront screens benefit from something that answers questions out loud. The embed widget is a single script, and the stack can run on a machine in the building.",
    points: [["One script tag", "Embed widget ships in the repo as sdk/embed/widget.js."], ["Runs locally", "The voice stack works with no cloud dependency."], ["Persona per location", "Different knowledge for each venue."]],
    steps: [["Create the persona", "Opening hours, directions and FAQs as knowledge."], ["Add the widget", "Point it at your API host."], ["Mount a screen", "Any browser works for the front end."]],
    script: [["u", "Where is the nearest charging station?"], ["a", "Past the front desk, on your left. There are four ports by the windows."]],
    window: "lobby-screen / welcome", chips: ["embed widget", "local voice stack"], caution: "Real-time lip-synced faces work at small sizes today. Live GPU-grade face rendering is coming, not shipped." },
];
export const findUseCase = (slug: string) => useCases.find((u) => u.slug === slug);
