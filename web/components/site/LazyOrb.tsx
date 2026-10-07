"use client";
import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import type { OrbState } from "./Orb";

const Orb = dynamic(() => import("./Orb"), { ssr: false });

type Props = { state?: OrbState; level?: number; className?: string; autoCycle?: boolean; density?: number };

/** Loads three.js only once the orb is near the viewport. Shows a static gradient until then (no layout shift). */
export default function LazyOrb({ className = "", ...rest }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [show, setShow] = useState(false);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    try { const c = document.createElement("canvas"); const gl = (c.getContext("webgl2") || c.getContext("webgl")) as WebGLRenderingContext | null; if (!gl) return; gl.getExtension("WEBGL_lose_context")?.loseContext(); } catch { return; }
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setShow(true); io.disconnect(); } }, { rootMargin: "200px" });
    io.observe(el); return () => io.disconnect();
  }, []);
  return (
    <div ref={ref} className={`relative ${className}`} aria-hidden>
      <div className="absolute inset-[18%] rounded-full bg-vocalface-gradient opacity-30 blur-3xl" />
      {show && <Orb {...rest} className="absolute inset-0" />}
    </div>
  );
}
