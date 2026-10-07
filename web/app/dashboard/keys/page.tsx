"use client";
import { useCallback, useEffect, useState } from "react";
import { Eye, EyeOff, Save, Plus, Trash2, KeyRound, RotateCw } from "lucide-react";
import { API_URL, ApiKey, api, fmtDate, getKey, setKey } from "@/lib/api";
import { Shell, CopyButton, ConfirmDialog, SecretOnce, Spinner, Empty, Badge, toast } from "@/components/ui";

type Lang = "curl" | "python" | "js";
type Snip = { title: string; curl: string; python: string; js: string };

function snippets(kk: string): Snip[] {
  const H = `x-api-key: ${kk}`;
  const py = (body: string) => `import requests\n\nr = requests.post("${API_URL}${body}`;
  return [
    { title: "Create a replica",
      curl: `curl -X POST ${API_URL}/v1/replicas -H "${H}" -H "content-type: application/json" \\\n  -d '{"name":"Me","train_video_url":"https://example.com/me.mp4"}'`,
      python: `${py(`/v1/replicas", headers={"x-api-key": "${kk}"},\n    json={"name": "Me", "train_video_url": "https://example.com/me.mp4"})\nprint(r.json())`)}`,
      js: `const r = await fetch("${API_URL}/v1/replicas", {\n  method: "POST",\n  headers: { "x-api-key": "${kk}", "content-type": "application/json" },\n  body: JSON.stringify({ name: "Me", train_video_url: "https://example.com/me.mp4" }),\n});\nconsole.log(await r.json());` },
    { title: "Create a persona",
      curl: `curl -X POST ${API_URL}/v1/personas -H "${H}" -H "content-type: application/json" \\\n  -d '{"name":"Coach","system_prompt":"You are a friendly coach.","replica_id":"r_..."}'`,
      python: `${py(`/v1/personas", headers={"x-api-key": "${kk}"},\n    json={"name": "Coach", "system_prompt": "You are a friendly coach.", "replica_id": "r_..."})\nprint(r.json())`)}`,
      js: `const r = await fetch("${API_URL}/v1/personas", {\n  method: "POST",\n  headers: { "x-api-key": "${kk}", "content-type": "application/json" },\n  body: JSON.stringify({ name: "Coach", system_prompt: "You are a friendly coach.", replica_id: "r_..." }),\n});\nconsole.log(await r.json());` },
    { title: "Start and end a conversation",
      curl: `curl -X POST ${API_URL}/v1/conversations -H "${H}" -H "content-type: application/json" -d '{"persona_id":"p_..."}'\ncurl -X POST ${API_URL}/v1/conversations/c_.../end -H "${H}"`,
      python: `import requests\nh = {"x-api-key": "${kk}"}\nc = requests.post("${API_URL}/v1/conversations", headers=h, json={"persona_id": "p_..."}).json()\n# ... talk via the playground / websocket ...\nrequests.post(f"${API_URL}/v1/conversations/{c['id']}/end", headers=h)`,
      js: `const h = { "x-api-key": "${kk}", "content-type": "application/json" };\nconst c = await (await fetch("${API_URL}/v1/conversations", { method: "POST", headers: h, body: JSON.stringify({ persona_id: "p_..." }) })).json();\nawait fetch(\`${API_URL}/v1/conversations/\${c.id}/end\`, { method: "POST", headers: h, body: "{}" });` },
    { title: "Generate a video",
      curl: `curl -X POST ${API_URL}/v1/videos -H "${H}" -H "content-type: application/json" -d '{"replica_id":"r_...","script":"Hello!"}'\ncurl ${API_URL}/v1/videos/v_... -H "${H}"`,
      python: `import requests\nh = {"x-api-key": "${kk}"}\nv = requests.post("${API_URL}/v1/videos", headers=h, json={"replica_id": "r_...", "script": "Hello!"}).json()\nprint(requests.get(f"${API_URL}/v1/videos/{v['id']}", headers=h).json())`,
      js: `const h = { "x-api-key": "${kk}", "content-type": "application/json" };\nconst v = await (await fetch("${API_URL}/v1/videos", { method: "POST", headers: h, body: JSON.stringify({ replica_id: "r_...", script: "Hello!" }) })).json();\nconsole.log(await (await fetch(\`${API_URL}/v1/videos/\${v.id}\`, { headers: h })).json());` },
    { title: "Check usage",
      curl: `curl ${API_URL}/v1/usage -H "${H}"`,
      python: `import requests\nprint(requests.get("${API_URL}/v1/usage", headers={"x-api-key": "${kk}"}).json())`,
      js: `const r = await fetch("${API_URL}/v1/usage", { headers: { "x-api-key": "${kk}" } });\nconsole.log(await r.json());` },
  ];
}

function KeyManager() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null); const [name, setName] = useState(""); const [busy, setBusy] = useState(false);
  const [secret, setSecret] = useState<{ title: string; value: string; note: string } | null>(null); const [rev, setRev] = useState<ApiKey | null>(null); const [rot, setRot] = useState(false);
  const load = useCallback(() => api<ApiKey[]>("/v1/keys").then(setKeys).catch((x) => { toast.error(x); setKeys((k) => k ?? []); }), []);
  useEffect(() => { load(); }, [load]);
  const mine = (k: ApiKey) => { const cur = getKey(); return !!cur && cur.startsWith(k.prefix) && !k.revoked_at; };
  async function create(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try { const k = await api<{ key: string }>("/v1/keys", { body: { name } }); setSecret({ title: "Copy your new API key now", value: k.key, note: "Only a hash is stored, so this is the one time you can see it." }); setName(""); load(); }
    catch (x) { toast.error(x); } finally { setBusy(false); }
  }
  const active = (keys ?? []).filter((k) => !k.revoked_at); const revoked = (keys ?? []).filter((k) => k.revoked_at);
  return (
    <div className="mb-10">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><h2 className="font-display text-2xl">Your keys</h2>
        <form onSubmit={create} className="flex gap-2"><input className="input !w-44" required placeholder="Key name, e.g. prod" aria-label="New key name" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn-grad" disabled={busy}>{busy ? <Spinner /> : <Plus size={15} />}Create key</button></form></div>
      {secret && <div className="mb-4"><SecretOnce title={secret.title} secret={secret.value} note={secret.note} onDone={() => setSecret(null)} /></div>}
      {keys === null ? <div className="h-24 animate-pulse rounded-2xl bg-white/5" /> : active.length === 0 ? <Empty kind="key" title="No active keys" hint="Create a key to call the API from your own code." /> : (
        <div className="overflow-x-auto rounded-2xl border border-white/10"><table className="w-full min-w-[34rem] text-left text-sm" data-testid="keys-table">
          <thead className="bg-white/[0.03] text-[10px] uppercase tracking-wider text-gray-500"><tr><th className="px-4 py-2.5">Name</th><th className="px-4 py-2.5">Key</th><th className="px-4 py-2.5">Created</th><th className="px-4 py-2.5">Last used</th><th /></tr></thead>
          <tbody>{active.map((k) => (
            <tr key={k.id} className="border-t border-white/5 bg-ink-2/60">
              <td className="px-4 py-3">{k.name}{mine(k) && <span className="ml-2 rounded-full bg-vocalface-violet/15 px-2 py-0.5 text-[10px] text-vocalface-violet">this browser</span>}</td>
              <td className="px-4 py-3 font-mono text-xs text-gray-400">{k.prefix}...</td>
              <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-400">{fmtDate(k.created_at)}</td>
              <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-400">{k.last_used_at ? fmtDate(k.last_used_at) : "never"}</td>
              <td className="px-4 py-3 text-right whitespace-nowrap">{k.legacy ? <button className="inline-flex items-center gap-1 text-xs text-vocalface-cyan hover:underline" onClick={() => setRot(true)}><RotateCw size={12} />Rotate</button>
                : <button aria-label={`Revoke ${k.name}`} className="inline-flex items-center gap-1 text-xs text-gray-400 hover:text-vocalface-rose" onClick={() => setRev(k)}><Trash2 size={13} />Revoke</button>}</td>
            </tr>))}</tbody></table></div>)}
      {revoked.length > 0 && <p className="mt-3 flex flex-wrap items-center gap-2 text-xs text-gray-500">Revoked: {revoked.map((k) => <span key={k.id} className="inline-flex items-center gap-1.5">{k.name} <Badge s="revoked" /></span>)}</p>}
      <ConfirmDialog open={!!rev} title="Revoke this key?" body={<>Requests using <b className="font-mono">{rev?.prefix}...</b> fail immediately, including open WebSocket sessions.{rev && mine(rev) && <b className="mt-2 block text-vocalface-amber">This is the key this browser uses. You will be signed out of the dashboard.</b>}</>} confirmLabel="Revoke key" onClose={() => setRev(null)}
        onConfirm={async () => { try { await api(`/v1/keys/${rev!.id}`, { method: "DELETE" }); toast.success("Key revoked."); setRev(null); load(); } catch (x) { toast.error(x); } }} />
      <ConfirmDialog open={rot} danger={false} title="Rotate the signup key?" body="The original key stops working immediately and a new one replaces it. This browser is updated automatically; update anywhere else you use it." confirmLabel="Rotate key" onClose={() => setRot(false)}
        onConfirm={async () => { try { const k = await api<{ key: string }>("/v1/keys/legacy/rotate", { method: "POST", body: {} }); setKey(k.key); setSecret({ title: "Your new signup key", value: k.key, note: "Saved in this browser. Copy it for any other place you use it." }); setRot(false); load(); } catch (x) { toast.error(x); } }} />
    </div>
  );
}

export default function Keys() {
  const [k, setK] = useState(""); const [show, setShow] = useState(false); const [lang, setLang] = useState<Lang>("curl");
  useEffect(() => setK(getKey()), []);
  const kk = k || "YOUR_API_KEY";
  const tabs: [Lang, string][] = [["curl", "cURL"], ["python", "Python"], ["js", "JavaScript"]];
  return (
    <Shell title="API keys & docs" subtitle="Everything you do in this dashboard is available over a plain REST API.">
      <KeyManager />
      <div className="card mb-8">
        <label className="label">Key used by this dashboard</label>
        <div className="flex flex-wrap gap-2">
          <input className="input min-w-0 basis-full font-mono sm:basis-0 sm:flex-1" type={show ? "text" : "password"} value={k} onChange={(e) => setK(e.target.value)} aria-label="API key" />
          <button className="btn-ghost !px-3" aria-label={show ? "Hide key" : "Show key"} onClick={() => setShow(!show)}>{show ? <EyeOff size={16} /> : <Eye size={16} />}</button>
          <CopyButton text={k} className="!px-3.5" />
          <button className="btn" onClick={() => { setKey(k); toast.success("Key saved in this browser."); }}><Save size={15} />Save</button>
        </div>
        <p className="mt-2 text-xs text-gray-500">Stored in this browser&apos;s localStorage. Paste a key here if you sign in on another device; the snippets below use it.</p>
      </div>

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-gray-400">Base URL <code className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-gray-200">{API_URL}</code> - send the key in the <code className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-gray-200">x-api-key</code> header.</p>
        <div className="inline-flex rounded-full border border-white/10 bg-white/5 p-0.5" role="tablist">
          {tabs.map(([id, l]) => <button key={id} role="tab" aria-selected={lang === id} onClick={() => setLang(id)} className={`rounded-full px-3.5 py-1 text-xs transition ${lang === id ? "bg-white text-ink" : "text-gray-400 hover:text-white"}`}>{l}</button>)}
        </div>
      </div>
      <div className="space-y-4">
        {snippets(kk).map((s) => (
          <div key={s.title} className="overflow-hidden rounded-2xl border border-white/10 bg-ink-2">
            <div className="flex items-center justify-between border-b border-white/10 bg-white/[0.03] px-4 py-2"><p className="text-sm font-medium">{s.title}</p><CopyButton text={s[lang]} /></div>
            <pre className="overflow-x-auto p-4 font-mono text-xs leading-relaxed text-gray-300">{s[lang]}</pre>
          </div>
        ))}
      </div>
      <p className="mt-6 text-sm text-gray-400">Other endpoints: GET /v1/replicas, /v1/personas, /v1/conversations, /v1/videos. Interactive docs at <a className="text-vocalface-rose hover:underline" href={API_URL + "/docs"} target="_blank" rel="noreferrer">{API_URL}/docs</a>.</p>
    </Shell>
  );
}
