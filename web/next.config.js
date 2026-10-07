// NEXT_DIST lets several dev servers run from this folder without clobbering each other's build cache.
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const isProd = process.env.NODE_ENV === "production";

// The dashboard keeps the API key in localStorage, so the page CSP matters: connect-src is limited to this origin + the API,
// so an injected script cannot send the key to an attacker's server with fetch/XHR/WebSocket. Next needs inline scripts for
// hydration, so script-src keeps 'unsafe-inline' (dev also needs eval for hot reload). Residual risk documented in docs/SECURITY.md.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isProd ? "" : " 'unsafe-eval'"}`,
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${API}`,
  "font-src 'self' data:",
  `connect-src 'self' ${API}${isProd ? "" : " ws: wss:"}`,
  `frame-src ${API}`,
  `media-src 'self' blob: ${API}`,
  "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'",
].join("; ");

module.exports = {
  reactStrictMode: true,
  distDir: process.env.NEXT_DIST || ".next",
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "Content-Security-Policy", value: csp },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Permissions-Policy", value: `camera=(self "${API}"), microphone=(self "${API}"), display-capture=(self "${API}")` },
    ] }];
  },
};
