"use client";
import { useEffect, useRef } from "react";
import { API_URL, getKey } from "@/lib/api";

/** Embeds the API's playground page. The API key is NOT put in the iframe URL (URLs end up in history, logs and Referer):
 *  it is handed over with postMessage, addressed to the API origin only, once the frame says it is ready. */
export default function PlaygroundFrame({ cid, className, allow = "camera; microphone; autoplay; display-capture" }: { cid: string; className?: string; allow?: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  useEffect(() => {
    const origin = new URL(API_URL, window.location.href).origin;
    const send = () => ref.current?.contentWindow?.postMessage({ type: "mirage-key", key: getKey() }, origin);
    const onMsg = (e: MessageEvent) => { if (e.source === ref.current?.contentWindow && e.origin === origin && e.data?.type === "mirage-playground-ready") send(); };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [cid]);
  const src = `${API_URL}/static/playground.html?cid=${encodeURIComponent(cid)}`;
  const onLoad = () => { const origin = new URL(API_URL, window.location.href).origin; ref.current?.contentWindow?.postMessage({ type: "mirage-key", key: getKey() }, origin); };
  return <iframe ref={ref} title="Playground" src={src} onLoad={onLoad} allow={allow} className={className} />;
}
