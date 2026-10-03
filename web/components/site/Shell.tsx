import Nav from "./Nav";
import { Footer } from "./Sections";

export default function Shell({ children }: { children: React.ReactNode }) {
  return (<div className="overflow-x-clip bg-ink"><a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[100] focus:rounded-full focus:bg-white focus:px-4 focus:py-2 focus:text-ink">Skip to content</a><Nav /><main id="main">{children}</main><Footer /></div>);
}
