export default function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-display text-2xl tracking-tight ${className}`}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/brand/emblem-nav.png" width={56} height={28} alt="" aria-hidden className="h-7 w-auto" />
      VocalFace
    </span>
  );
}
