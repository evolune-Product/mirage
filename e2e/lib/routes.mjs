// Discover static routes from the Next app dir (skips dynamic [param] segments).
import fs from 'node:fs';
import path from 'node:path';
export function smokeRoutes(appDir, smoke) {
  const out = [];
  (function walk(dir, url) {
    if (fs.existsSync(path.join(dir, 'page.tsx')) || fs.existsSync(path.join(dir, 'page.jsx'))) out.push(url || '/');
    for (const d of fs.readdirSync(dir, { withFileTypes: true })) {
      if (d.isDirectory() && !/^[\[(_@]/.test(d.name) && !d.name.startsWith('.')) walk(path.join(dir, d.name), `${url}/${d.name}`);
    }
  })(appDir, '');
  out.sort();
  if (smoke) return out.filter((r) => /^\/(dashboard.*|signup|pricing|docs)?$/.test(r) || r === '/');
  return out;
}
