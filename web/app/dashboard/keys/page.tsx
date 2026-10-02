"use client";
import { useEffect, useState } from "react";
import { API_URL, getKey, setKey } from "@/lib/api";
import { Shell } from "@/components/ui";
const Code = ({ c }: { c: string }) => <pre className="overflow-x-auto rounded-lg border border-line bg-bg p-3 text-xs text-gray-300">{c}</pre>;
export default function Keys() {
  const [k, setK] = useState(""); const [show, setShow] = useState(false);
  useEffect(() => setK(getKey()), []);
  const kk = k || "YOUR_API_KEY";
  return (<Shell title="API keys & docs"><div className="card mb-6"><label className="label">Your API key</label>
    <div className="flex gap-2"><input className="input font-mono" type={show ? "text" : "password"} value={k} onChange={e => setK(e.target.value)} /><button className="btn-ghost" onClick={() => setShow(!show)}>{show ? "Hide" : "Show"}</button><button className="btn" onClick={() => setKey(k)}>Save</button></div>
    <p className="mt-2 text-xs text-gray-500">Stored in localStorage. Multiple keys per account are not supported by the API yet.</p></div>
    <div className="card space-y-3"><p className="text-sm text-gray-400">Base URL: <code>{API_URL}</code>. Send the key in the <code>x-api-key</code> header.</p>
      <p className="label">Create a replica</p><Code c={`curl -X POST ${API_URL}/v1/replicas -H "x-api-key: ${kk}" -H "content-type: application/json" \\\n  -d '{"name":"Me","train_video_url":"https://example.com/me.mp4"}'`} />
      <p className="label">Create a persona</p><Code c={`curl -X POST ${API_URL}/v1/personas -H "x-api-key: ${kk}" -H "content-type: application/json" \\\n  -d '{"name":"Coach","system_prompt":"You are a friendly coach.","replica_id":"r_..."}'`} />
      <p className="label">Start / end a conversation</p><Code c={`curl -X POST ${API_URL}/v1/conversations -H "x-api-key: ${kk}" -H "content-type: application/json" -d '{"persona_id":"p_..."}'\ncurl -X POST ${API_URL}/v1/conversations/c_.../end -H "x-api-key: ${kk}"`} />
      <p className="label">Generate a video</p><Code c={`curl -X POST ${API_URL}/v1/videos -H "x-api-key: ${kk}" -H "content-type: application/json" -d '{"replica_id":"r_...","script":"Hello!"}'\ncurl ${API_URL}/v1/videos/v_... -H "x-api-key: ${kk}"`} />
      <p className="label">Usage</p><Code c={`curl ${API_URL}/v1/usage -H "x-api-key: ${kk}"`} />
      <p className="text-sm text-gray-400">Other endpoints: GET /v1/replicas, /v1/replicas/&#123;id&#125;, /v1/personas. Interactive docs at <a className="text-accent" href={API_URL + "/docs"} target="_blank">{API_URL}/docs</a>.</p></div></Shell>);
}
