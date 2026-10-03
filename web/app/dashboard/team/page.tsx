"use client";
import { useCallback, useEffect, useState } from "react";
import { Check, Crown, LogOut, Mail, Plus, Trash2, UserPlus, Users } from "lucide-react";
import { api, errText, fmtDate, getWorkspace, setWorkspace, Workspace } from "@/lib/api";
import { Badge, ConfirmDialog, Empty, Field, SecretOnce, Section, Shell, Skeleton, Spinner, toast } from "@/components/ui";
import { Callout } from "@/components/kit";

type Member = { account_id: string; email: string; role: string; joined_at: string };
type Invite = { id: string; email: string; role: string; expires_at: string; status: string };
const ROLE_HINT: Record<string, string> = { owner: "Everything, including billing, API keys and deleting the workspace.", admin: "Everything except billing, API keys, account deletion and workspace ownership.", member: "Use the workspace (replicas, personas, conversations, videos). Cannot change billing or keys." };

function Members({ ws, me, onLeft }: { ws: Workspace; me: string; onLeft: () => void }) {
  const [mem, setMem] = useState<Member[] | null>(null); const [inv, setInv] = useState<Invite[]>([]); const [email, setEmail] = useState(""); const [role, setRole] = useState("member"); const [busy, setBusy] = useState(false);
  const [secret, setSecret] = useState<{ email: string; token: string } | null>(null); const [rm, setRm] = useState<Member | null>(null); const [err, setErr] = useState("");
  const canInvite = ws.role === "owner" || ws.role === "admin";
  const load = useCallback(() => {
    api<Member[]>(`/v1/workspaces/${ws.id}/members`).then(setMem).catch((x) => { toast.error(x); setMem([]); });
    if (canInvite) api<Invite[]>(`/v1/workspaces/${ws.id}/invites`).then(setInv).catch(() => {});
  }, [ws.id, canInvite]);
  useEffect(() => { setMem(null); load(); }, [load]);
  async function invite(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try { const r = await api<{ token: string; email: string }>(`/v1/workspaces/${ws.id}/invites`, { body: { email, role } }); setSecret({ email: r.email, token: r.token }); setEmail(""); load(); }
    catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-6">
      <Section title="Members" icon={<Users size={16} className="text-mirage-cyan" />} hint={ROLE_HINT[ws.role] ? `Your role: ${ws.role}. ${ROLE_HINT[ws.role]}` : undefined}>
        {mem === null ? <Skeleton className="h-24" /> : (
          <ul className="divide-y divide-white/5 overflow-hidden rounded-xl border border-white/10" data-testid="members">{mem.map((m) => (
            <li key={m.account_id} className="flex flex-wrap items-center gap-3 bg-ink-2/60 px-4 py-3">
              <span className="grid h-8 w-8 place-items-center rounded-full bg-white/10 text-xs uppercase">{(m.email || "?")[0]}</span>
              <div className="min-w-0 flex-1"><p className="truncate text-sm">{m.email || m.account_id}{m.account_id === me && <span className="ml-2 text-xs text-gray-500">(you)</span>}</p><p className="text-xs text-gray-500">joined {fmtDate(m.joined_at)}</p></div>
              {ws.role === "owner" && m.role !== "owner" ? (
                <select className="input !w-28 !py-1 text-xs" aria-label={`Role of ${m.email}`} value={m.role} onChange={async (e) => { try { await api(`/v1/workspaces/${ws.id}/members/${m.account_id}`, { method: "PUT", body: { role: e.target.value } }); toast.success("Role updated."); load(); } catch (x) { toast.error(x); } }}><option value="admin">admin</option><option value="member">member</option></select>
              ) : <span className="inline-flex items-center gap-1 rounded-full bg-white/5 px-2.5 py-0.5 text-xs">{m.role === "owner" && <Crown size={11} className="text-mirage-amber" />}{m.role}</span>}
              {m.role !== "owner" && (ws.role === "owner" || (ws.role === "admin" && m.role === "member") || m.account_id === me) && <button aria-label={`Remove ${m.email}`} className="rounded-lg p-1.5 text-gray-500 hover:bg-mirage-rose/10 hover:text-mirage-rose" onClick={() => setRm(m)}>{m.account_id === me ? <LogOut size={15} /> : <Trash2 size={15} />}</button>}
            </li>))}</ul>)}
      </Section>
      {canInvite && (<Section title="Invite someone" icon={<UserPlus size={16} className="text-mirage-mint" />} hint="Mirage does not send email yet: you get a one-time invite code to pass to them. They paste it under Join a workspace on this page (they need their own Mirage account).">
        {secret && <div className="mb-3"><SecretOnce title={`Invite code for ${secret.email}`} secret={secret.token} note="Shown once. Send it privately; it expires in a few days." onDone={() => setSecret(null)} /></div>}
        <form onSubmit={invite} className="grid gap-3 rounded-xl bg-white/[0.03] p-4 sm:grid-cols-[1fr_9rem_auto] sm:items-end">
          <Field label="Email"><input className="input" type="email" required placeholder="teammate@company.com" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Invite email" /></Field>
          <Field label="Role"><select className="input" value={role} onChange={(e) => setRole(e.target.value)} aria-label="Invite role"><option value="member">member</option>{ws.role === "owner" && <option value="admin">admin</option>}</select></Field>
          <button className="btn-grad" disabled={busy}>{busy ? <Spinner /> : <Mail size={15} />}Create invite</button>
        </form>
        {err && <div className="mt-2"><Callout tone="bad">{err}</Callout></div>}
        {inv.filter((i) => i.status === "pending").length > 0 && <ul className="mt-3 space-y-1.5 text-sm">{inv.filter((i) => i.status === "pending").map((i) => (
          <li key={i.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-white/10 px-3 py-2"><span className="min-w-0 flex-1 truncate">{i.email}</span><Badge s="pending" /><span className="text-xs text-gray-500">{i.role}, expires {fmtDate(i.expires_at)}</span>
            <button className="text-xs text-mirage-rose hover:underline" onClick={async () => { try { await api(`/v1/workspaces/${ws.id}/invites/${i.id}`, { method: "DELETE" }); load(); } catch (x) { toast.error(x); } }}>Revoke</button></li>))}</ul>}
      </Section>)}
      <ConfirmDialog open={!!rm} title={rm?.account_id === me ? "Leave this workspace?" : "Remove this member?"} body={rm?.account_id === me ? "You lose access to its replicas, personas and videos." : <>{rm?.email} loses access immediately.</>} confirmLabel={rm?.account_id === me ? "Leave" : "Remove"} onClose={() => setRm(null)}
        onConfirm={async () => { try { await api(`/v1/workspaces/${ws.id}/members/${rm!.account_id}`, { method: "DELETE" }); const left = rm!.account_id === me; setRm(null); toast.success(left ? "You left the workspace." : "Member removed."); if (left) onLeft(); else load(); } catch (x) { toast.error(x); setRm(null); } }} />
    </div>
  );
}

export default function Team() {
  const [list, setList] = useState<Workspace[] | null>(null); const [sel, setSel] = useState(""); const [active, setActive] = useState(""); const [name, setName] = useState(""); const [busy, setBusy] = useState(false);
  const [token, setToken] = useState(""); const [joinErr, setJoinErr] = useState(""); const [del, setDel] = useState(false); const [me, setMe] = useState("");
  useEffect(() => { setActive(getWorkspace()); }, []);
  const load = useCallback(() => api<Workspace[]>("/v1/workspaces").then((l) => { setList(l); setSel((s) => (l.some((w) => w.id === s) ? s : l[0]?.id ?? "")); setMe(l.find((w) => w.role === "owner")?.owner_account_id ?? ""); }).catch((x) => { toast.error(x); setList([]); }), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { // our own account id: from any owner row, or (when we only joined others' workspaces) from the member list
    if (me || !list?.length) return; const w = list[0]; api<{ account_id: string; email: string }[]>(`/v1/workspaces/${w.id}/members`).then(() => {}).catch(() => {}); }, [me, list]);
  const cur = list?.find((w) => w.id === sel);
  async function create(e: React.FormEvent) { e.preventDefault(); setBusy(true); try { const w = await api<Workspace>("/v1/workspaces", { body: { name } }); setName(""); toast.success("Workspace created."); await load(); setSel(w.id); } catch (x) { toast.error(x); } finally { setBusy(false); } }
  async function join(e: React.FormEvent) { e.preventDefault(); setJoinErr(""); try { const w = await api<Workspace>("/v1/workspaces/invites/accept", { body: { token: token.trim() } }); setToken(""); toast.success(`Joined ${w.name} as ${w.role}.`); await load(); setSel(w.id); } catch (x) { setJoinErr(errText(x)); } }
  const use = (id: string) => { setWorkspace(id); toast.success(id ? "Switched workspace. Reloading..." : "Back on your personal account. Reloading..."); setTimeout(() => location.reload(), 500); };
  return (
    <Shell title="Team" subtitle="Share replicas, personas and credits with teammates. Switch between your personal account and a workspace at any time.">
      {active && <div className="mb-6"><Callout tone="info" title="You are working inside a shared workspace">Everything you see on other pages (replicas, videos, credits) belongs to that workspace. <button className="underline" onClick={() => use("")}>Switch back to my personal account</button>. Live conversations started here run on the owner&apos;s account; if the conversation window cannot connect while in a workspace, switch back to your personal account to talk.</Callout></div>}
      <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
        <div className="space-y-4">
          {list === null ? <Skeleton className="h-40" /> : list.length === 0 ? <Empty kind="persona" title="No workspaces yet" hint="Create one to invite teammates." /> : (
            <ul className="space-y-2" data-testid="ws-list">{list.map((w) => (
              <li key={w.id}><button onClick={() => setSel(w.id)} aria-pressed={sel === w.id} className={`w-full rounded-xl border p-3.5 text-left transition ${sel === w.id ? "border-mirage-violet/50 bg-mirage-violet/10" : "border-white/10 bg-white/[0.03] hover:bg-white/[0.06]"}`}>
                <div className="flex items-center gap-2"><p className="min-w-0 flex-1 truncate font-medium">{w.name}</p>{active === w.id && <span className="rounded-full bg-mirage-mint/15 px-2 py-0.5 text-[10px] text-mirage-mint">in use</span>}</div>
                <p className="mt-0.5 text-xs text-gray-500">{w.role} - created {fmtDate(w.created_at)}</p></button></li>))}</ul>)}
          <form onSubmit={create} className="space-y-2 rounded-xl bg-white/[0.03] p-3.5"><Field label="New workspace"><input className="input" required maxLength={80} placeholder="e.g. Acme marketing" value={name} onChange={(e) => setName(e.target.value)} aria-label="Workspace name" /></Field><button className="btn w-full" disabled={busy}>{busy ? <Spinner size={14} /> : <Plus size={14} />}Create workspace</button></form>
          <form onSubmit={join} className="space-y-2 rounded-xl bg-white/[0.03] p-3.5"><Field label="Join a workspace" hint="Paste the invite code you were sent."><input className="input font-mono text-xs" required placeholder="wsinv_..." value={token} onChange={(e) => setToken(e.target.value)} aria-label="Invite code" /></Field><button className="btn w-full">Join</button>{joinErr && <p className="text-xs text-mirage-rose" role="alert">{joinErr}</p>}</form>
        </div>
        <div>
          {cur ? (<>
            <div className="mb-6 flex flex-wrap items-center gap-3 rounded-2xl border border-white/10 bg-ink-2/60 p-4">
              <div className="min-w-0 flex-1"><h2 className="font-display text-3xl">{cur.name}</h2><p className="font-mono text-xs text-gray-500">{cur.id}</p></div>
              {active === cur.id ? <button className="btn-ghost" onClick={() => use("")}>Use my personal account</button> : <button className="btn-grad" onClick={() => use(cur.id)}><Check size={15} />Work in this workspace</button>}
            </div>
            <Members ws={cur} me={me} onLeft={() => { if (active === cur.id) use(""); else load(); }} />
            {cur.role === "owner" && <div className="mt-8 border-t border-white/10 pt-5"><p className="text-sm font-medium text-mirage-rose">Danger zone</p><button className="btn-ghost mt-3 !border-mirage-rose/40 !text-mirage-rose hover:!bg-mirage-rose/10" onClick={() => setDel(true)}><Trash2 size={14} />Delete workspace</button></div>}
            <ConfirmDialog open={del} title="Delete this workspace?" typed={cur.name} body="Members lose access. The replicas and data stay with your own account." confirmLabel="Delete workspace" onClose={() => setDel(false)}
              onConfirm={async () => { try { await api(`/v1/workspaces/${cur.id}`, { method: "DELETE" }); setDel(false); toast.success("Workspace deleted."); if (active === cur.id) use(""); else load(); } catch (x) { toast.error(x); } }} />
          </>) : list && list.length > 0 ? null : (list ? <Empty kind="persona" title="Work with a team" hint="Create a workspace, invite teammates by email code, and share one set of replicas, personas and credits." /> : <Skeleton className="h-64" />)}
        </div>
      </div>
    </Shell>
  );
}
