"use client";
import { useEffect, useState } from "react";
import { Eye, EyeOff, Save } from "lucide-react";
import { API_URL, getKey, setKey } from "@/lib/api";
import { Shell, CopyButton, toast } from "@/components/ui";

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

export default function Keys() {
  const [k, setK] = useState(""); const [show, setShow] = useState(false); const [lang, setLang] = useState<Lang>("curl");
  useEffect(() => setK(getKey()), []);
  const kk = k || "YOUR_API_KEY";
  const tabs: [Lang, string][] = [["curl", "cURL"], ["python", "Python"], ["js", "JavaScript"]];
  return (
    <Shell title="API keys & docs" subtitle="Everything you do in this dashboard is available over a plain REST API.">
      <div className="card mb-8">
        <label className="label">Your API key</label>
        <div className="flex flex-wrap gap-2">
          <input className="input min-w-0 basis-full font-mono sm:basis-0 sm:flex-1" type={show ? "text" : "password"} value={k} onChange={(e) => setK(e.target.value)} aria-label="API key" />
          <button className="btn-ghost !px-3" aria-label={show ? "Hide key" : "Show key"} onClick={() => setShow(!show)}>{show ? <EyeOff size={16} /> : <Eye size={16} />}</button>
          <CopyButton text={k} className="!px-3.5" />
          <button className="btn" onClick={() => { setKey(k); toast.success("Key saved in this browser."); }}><Save size={15} />Save</button>
        </div>
        <p className="mt-2 text-xs text-gray-500">Stored in this browser&apos;s localStorage. Multiple keys per account are not supported by the API yet.</p>
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
      <p className="mt-6 text-sm text-gray-400">Other endpoints: GET /v1/replicas, /v1/personas, /v1/conversations, /v1/videos. Interactive docs at <a className="text-mirage-rose hover:underline" href={API_URL + "/docs"} target="_blank" rel="noreferrer">{API_URL}/docs</a>.</p>
    </Shell>
  );
}
