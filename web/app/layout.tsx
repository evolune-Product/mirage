import "./globals.css";
import type { Metadata } from "next";
import { Instrument_Serif, Inter, JetBrains_Mono } from "next/font/google";

const display = Instrument_Serif({ subsets: ["latin"], weight: "400", style: ["normal", "italic"], variable: "--font-display" });
const sans = Inter({ subsets: ["latin"], variable: "--font-sans" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

const site = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";
const desc = "Real-time face-to-face AI agents, digital replicas and video generation. Open-core, self-hostable, built to cost less.";
export const metadata: Metadata = {
  metadataBase: new URL(site),
  title: { default: "VocalFace - conversational video AI you can own", template: "%s" },
  description: desc,
  alternates: { canonical: "/" },
  openGraph: { type: "website", siteName: "VocalFace", title: "VocalFace - conversational video AI you can own", description: desc },
  twitter: { card: "summary_large_image", title: "VocalFace - conversational video AI you can own", description: desc },
};
export const viewport = { themeColor: "#090b10" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable} ${mono.variable}`}>
      <body className="antialiased">{children}</body>
    </html>
  );
}
