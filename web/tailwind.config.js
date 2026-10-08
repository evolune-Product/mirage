module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#090b10", 2: "#11141b", 3: "#181c25" },
        cream: { DEFAULT: "#f6f1e8", 2: "#ece5d8" },
        line: "#252a35",
        vocalface: { amber: "#7dd3fc", rose: "#bcd4ea", violet: "#4f6fa8", cyan: "#5ce1e6", mint: "#9cf0c4" },
        // legacy names used by the old dashboard
        bg: "#090b10", panel: "#11141b", accent: "#bcd4ea",
      },
      fontFamily: {
        display: ["var(--font-display)", "Georgia", "serif"],
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      backgroundImage: {
        "vocalface-gradient": "linear-gradient(120deg,#ffffff 0%,#c9d6e6 50%,#7dd3fc 100%)",
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
