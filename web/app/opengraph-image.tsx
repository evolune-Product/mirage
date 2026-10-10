import { ImageResponse } from "next/og";
import { readFileSync } from "fs";
import { join } from "path";
export const alt = "VocalFace - conversational video AI you can own";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export default function Image() {
  const emblem = `data:image/png;base64,${readFileSync(join(process.cwd(), "public/brand/emblem-og.png")).toString("base64")}`;
  return new ImageResponse(
    (<div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "center", padding: 80, background: "#090b10", color: "#fff", position: "relative" }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={emblem} width={560} height={280} alt="" style={{ position: "absolute", right: 40, top: 150, opacity: 0.95 }} />
      <div style={{ fontSize: 40, color: "#7dd3fc", display: "flex" }}>VocalFace</div>
      <div style={{ fontSize: 76, lineHeight: 1.05, marginTop: 24, maxWidth: 640, display: "flex" }}>Conversational video AI you can own.</div>
      <div style={{ fontSize: 30, color: "#b9b5c7", marginTop: 32, display: "flex" }}>Open-core. Self-hostable. Consent-first.</div>
    </div>), size);
}
