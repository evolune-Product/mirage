import "./globals.css";
import type { Metadata } from "next";
export const metadata: Metadata = { title: "Mirage - open-core conversational video AI", description: "Real-time face-to-face AI agents, replicas and video generation. Cheaper than Tavus, open-core, self-hostable." };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body className="antialiased">{children}</body></html>;
}
