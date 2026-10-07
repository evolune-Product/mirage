module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#0b0a10", 2: "#12111a", 3: "#1a1824" },
        cream: { DEFAULT: "#f6f1e8", 2: "#ece5d8" },
        line: "#2a2733",
        vocalface: { amber: "#ff9e5e", rose: "#ff4d8d", violet: "#7c5cff", cyan: "#5ce1e6", mint: "#9cf0c4" },
        // legacy names used by the old dashboard
        bg: "#0b0a10", panel: "#12111a", accent: "#ff6b9d",
      },
      fontFamily: {
        display: ["var(--font-display)", "Georgia", "serif"],
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      backgroundImage: {
        "vocalface-gradient": "linear-gradient(120deg,#ff9e5e 0%,#ff4d8d 45%,#7c5cff 100%)",
        "grid-faint": "linear-gradient(to right,rgba(255,255,255,.04) 1px,transparent 1px),linear-gradient(to bottom,rgba(255,255,255,.04) 1px,transparent 1px)",
      },
      keyframes: {
        float: { "0%,100%": { transform: "translateY(0)" }, "50%": { transform: "translateY(-10px)" } },
        marquee: { from: { transform: "translateX(0)" }, to: { transform: "translateX(-50%)" } },
        shimmer: { from: { backgroundPosition: "200% 0" }, to: { backgroundPosition: "-200% 0" } },
      },
      animation: { float: "float 6s ease-in-out infinite", marquee: "marquee 30s linear infinite", shimmer: "shimmer 3s linear infinite" },
    },
  },
  plugins: [],
};
