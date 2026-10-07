"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowRight, KeyRound, Loader2, Mail, ShieldCheck, Sparkles } from "lucide-react";
import { API_URL, api, setKey } from "@/lib/api";

/** Optional proof of work (only when the server sets VOCALFACE_SIGNUP_POW_BITS): find a nonce so sha256(challenge:nonce) has `bits` leading zero bits. */
async function solvePow(): Promise<{ pow_challenge?: string; pow_nonce?: string }> {
  const ch = await fetch(`${API_URL}/v1/signup/challenge`).then((r) => r.json()).catch(() => ({ bits: 0 }));
  if (!ch.bits) return {};
  const enc = new TextEncoder();
  for (let i = 0; ; i++) {
    const d = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(`${ch.challenge}:${i}`)));
    let n = 0; for (const b of d) { if (b === 0) { n += 8; continue; } n += Math.clz32(b) - 24; break; }
    if (n >= ch.bits) return { pow_challenge: ch.challenge, pow_nonce: String(i) };
  }
}
import Orb from "@/components/site/Orb";
import Logo from "@/components/site/Logo";
import { Toaster, toast } from "@/components/ui";

export default function Signup() {
  const r = useRouter();
  const [mode, setMode] = useState<"new" | "have">("new");
  const [email, setEmail] = useState(""); const [pasted, setPasted] = useState("");
  const [hp, setHp] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);

  async function go(e: React.FormEvent) {
    e.preventDefault(); setErr("");
    if (!/^\S+@\S+\.\S+$/.test(email.trim())) return setErr("Enter a valid email address.");
    setBusy(true);
    try { const j = await api<{ api_key: string }>("/v1/signup", { body: { email: email.trim(), website: hp, ...(await solvePow()) }, key: "" }); setKey(j.api_key); r.push("/dashboard"); }
    catch (x) { setErr((x as Error).message); toast.error(x); } finally { setBusy(false); }
  }
  async function useKey(e: React.FormEvent) {
    e.preventDefault(); setErr("");
    const k = pasted.trim();
    if (!k) return setErr("Paste your API key (starts with mk_).");
    setBusy(true);
    try { await api("/v1/usage", { key: k }); setKey(k); r.push("/dashboard"); }
    catch { setErr("That key was not accepted. Check it and try again."); } finally { setBusy(false); }
  }

  return (
    <div className="grid min-h-screen bg-ink lg:grid-cols-2">
      <div className="relative flex flex-col px-6 py-8 sm:px-12">
        <Link href="/"><Logo /></Link>
        <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center py-12">
          <p className="eyebrow mb-3">{mode === "new" ? "Start free" : "Welcome back"}</p>
          <h1 className="font-display text-5xl leading-[1.05]">{mode === "new" ? <>Meet your first <span className="text-grad italic">face-to-face</span> agent.</> : <>Paste your <span className="text-grad italic">key</span>.</>}</h1>
          <p className="mt-3 text-sm text-gray-400">{mode === "new" ? "Get an API key with 600 free seconds. No card. The key is stored in this browser only." : "Your key is checked against the API, then saved in this browser only."}</p>
          <AnimatePresence mode="wait">
            {mode === "new" ? (
              <motion.form key="new" onSubmit={go} noValidate initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} className="mt-8">
                <label className="label" htmlFor="email">Email</label>
                <div className="relative"><Mail size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-500" />
                  <input id="email" className="input !pl-10" type="email" autoComplete="email" placeholder="you@company.com" required value={email} onChange={(e) => { setEmail(e.target.value); setErr(""); }} /></div>
                <input type="text" name="website" tabIndex={-1} autoComplete="off" aria-hidden="true" value={hp} onChange={(e) => setHp(e.target.value)} style={{ position: "absolute", left: "-9999px", width: 1, height: 1, opacity: 0 }} />
                {err && <p className="mt-2 text-xs text-vocalface-rose">{err}</p>}
                <button className="btn-grad mt-5 w-full !py-3" disabled={busy}>{busy ? <><Loader2 size={16} className="animate-spin" />Creating your key...</> : <>Sign up<ArrowRight size={16} /></>}</button>
              </motion.form>
            ) : (
              <motion.form key="have" onSubmit={useKey} noValidate initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} className="mt-8">
                <label className="label" htmlFor="key">API key</label>
                <div className="relative"><KeyRound size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-500" />
                  <input id="key" className="input !pl-10 font-mono" placeholder="mk_..." value={pasted} onChange={(e) => { setPasted(e.target.value); setErr(""); }} autoComplete="off" spellCheck={false} /></div>
                {err && <p className="mt-2 text-xs text-vocalface-rose">{err}</p>}
                <button className="btn-grad mt-5 w-full !py-3" disabled={busy}>{busy ? <><Loader2 size={16} className="animate-spin" />Checking...</> : <>Continue to dashboard<ArrowRight size={16} /></>}</button>
              </motion.form>
            )}
          </AnimatePresence>
          <button className="mt-5 text-left text-sm text-gray-400 hover:text-white" onClick={() => { setMode(mode === "new" ? "have" : "new"); setErr(""); }}>
            {mode === "new" ? <>Already have a key? <span className="text-vocalface-rose underline-offset-4 hover:underline">Paste it here</span></> : <>No key yet? <span className="text-vocalface-rose underline-offset-4 hover:underline">Get one free</span></>}
          </button>
          <div className="mt-10 flex flex-wrap gap-x-6 gap-y-2 text-xs text-gray-500">
            <span className="inline-flex items-center gap-1.5"><ShieldCheck size={14} />Consent-first replicas</span>
            <span className="inline-flex items-center gap-1.5"><Sparkles size={14} />Open-core, self-hostable</span>
          </div>
        </div>
      </div>
      <div className="relative min-h-[380px] overflow-hidden border-t border-white/[0.07] bg-ink-2 lg:min-h-0 lg:border-l lg:border-t-0">
        <div className="absolute inset-0 bg-[radial-gradient(40rem_30rem_at_50%_40%,rgba(124,92,255,.22),transparent),radial-gradient(30rem_20rem_at_20%_90%,rgba(255,77,141,.15),transparent)]" />
        <div className="absolute inset-0 bg-grid-faint [background-size:48px_48px] [mask-image:radial-gradient(circle_at_center,black,transparent_70%)]" />
        <Orb state="idle" autoCycle className="absolute inset-0" />
        <div className="absolute inset-x-0 bottom-0 p-6 sm:p-12">
          <p className="font-display text-2xl leading-tight sm:text-4xl">It listens, thinks and answers <span className="text-grad italic">with a face</span> - for a fraction of the cost.</p>
          <p className="mt-3 font-mono text-xs uppercase tracking-[0.2em] text-gray-400">Real-time conversational video AI</p>
        </div>
      </div>
      <Toaster />
    </div>
  );
}
