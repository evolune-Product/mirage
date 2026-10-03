import type { MetadataRoute } from "next";
import { useCases } from "@/lib/usecases";
import { docPages } from "@/lib/docs";
const site = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";
export default function sitemap(): MetadataRoute.Sitemap {
  const paths = ["/", "/pricing", "/docs", "/use-cases", "/security", "/about", "/changelog", "/compare", "/terms", "/privacy",
    ...docPages.map((d) => `/docs/${d.slug}`), ...useCases.map((u) => `/use-cases/${u.slug}`)];
  return paths.map((p) => ({ url: `${site}${p}`, changeFrequency: "weekly", priority: p === "/" ? 1 : 0.6 }));
}
