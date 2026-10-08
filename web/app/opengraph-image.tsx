import { ImageResponse } from "next/og";
export const alt = "VocalFace - conversational video AI you can own";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export default function Image() {
  return new ImageResponse(
    (<div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "center", padding: 80, background: "#090b10", color: "#fff", position: "relative" }}>
      <div style={{ position: "absolute", right: -80, top: -80, width: 560, height: 560, borderRadius: 560, background: "linear-gradient(135deg,#7dd3fc,#bcd4ea 50%,#4f6fa8)", opacity: 0.9, display: "flex" }} />
      <div style={{ fontSize: 40, color: "#7dd3fc", display: "flex" }}>VocalFace</div>
      <div style={{ fontSize: 84, lineHeight: 1.05, marginTop: 24, maxWidth: 800, display: "flex" }}>Conversational video AI you can own.</div>
      <div style={{ fontSize: 30, color: "#b9b5c7", marginTop: 32, display: "flex" }}>Open-core. Self-hostable. Consent-first.</div>
    </div>), size);
}
