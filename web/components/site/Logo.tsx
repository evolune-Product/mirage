export default function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-display text-2xl tracking-tight ${className}`}>
      <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#ff9e5e" /><stop offset=".5" stopColor="#ff4d8d" /><stop offset="1" stopColor="#7c5cff" /></linearGradient></defs>
        <circle cx="16" cy="16" r="14" fill="url(#lg)" /><path d="M5 18c4-5 8-5 11 0s7 5 11 0" stroke="#0b0a10" strokeWidth="2.4" fill="none" strokeLinecap="round" /></svg>
      VocalFace
    </span>
  );
}
