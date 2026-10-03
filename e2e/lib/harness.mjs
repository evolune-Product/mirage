// Process management, free ports, readiness waits, retrying API client, result reporting.
import { spawn, execFileSync } from 'node:child_process';
import net from 'node:net';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

export const E2E_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const ROOT = path.resolve(E2E_DIR, '..');
export const ART = path.join(E2E_DIR, 'artifacts');
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const PY = path.join(ROOT, 'backend/.venv/bin/python');

export function freePort() {
  return new Promise((res, rej) => {
    const s = net.createServer();
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
    s.on('error', rej);
  });
}

const procs = [];
export function start(name, cmd, args, { cwd, env, logDir }) {
  const log = fs.openSync(path.join(logDir, `${name}.log`), 'w');
  const p = spawn(cmd, args, { cwd, env: { ...process.env, ...env }, stdio: ['ignore', log, log], detached: false });
  p.on('error', (e) => console.error(`[${name}] failed to start: ${e.message}`));
  procs.push({ name, p });
  return p;
}
export function stopAll() {
  for (const { p } of procs) { try { p.kill('SIGTERM'); } catch {} }
  setTimeout(() => { for (const { p } of procs) { try { p.kill('SIGKILL'); } catch {} } }, 3000).unref();
}

export async function waitHttp(url, { timeout = 120000, name = url, ok = (r) => r.status < 500 } = {}) {
  const t0 = Date.now(); let last = '';
  while (Date.now() - t0 < timeout) {
    try { const r = await fetch(url, { headers: { connection: 'close' } }); if (ok(r)) return; last = `HTTP ${r.status}`; }
    catch (e) { last = e.cause?.code || e.message; }
    await sleep(500);
  }
  throw new Error(`${name} not ready after ${timeout / 1000}s (${last}); see e2e/artifacts/logs`);
}

export async function until(fn, { timeout = 60000, every = 500, what = 'condition' } = {}) {
  const t0 = Date.now(); let last;
  while (Date.now() - t0 < timeout) {
    try { const v = await fn(); if (v) return v; last = v; } catch (e) { last = e; }
    await sleep(every);
  }
  throw new Error(`timed out after ${timeout / 1000}s waiting for ${what}${last instanceof Error ? ` (${last.message})` : ''}`);
}

/** fetch with retries for keep-alive resets / transient connection errors (not for HTTP error statuses). */
export function apiClient(base) {
  const call = async (method, url, key, body, { raw = false, form = null, expect } = {}) => {
    let err;
    for (let i = 0; i < 4; i++) {
      try {
        const headers = { connection: 'close' };
        if (key) headers['x-api-key'] = key;
        let payload;
        if (form) payload = form; else if (body !== undefined) { headers['content-type'] = 'application/json'; payload = JSON.stringify(body); }
        const r = await fetch(base + url, { method, headers, body: payload });
        if (raw) return r;
        const txt = await r.text(); let j; try { j = JSON.parse(txt); } catch { j = txt; }
        if (expect !== undefined ? r.status !== expect : r.status >= 400) {
          throw Object.assign(new Error(`${method} ${url} -> ${r.status} ${typeof j === 'string' ? j.slice(0, 200) : JSON.stringify(j).slice(0, 300)}`), { status: r.status, body: j, final: true });
        }
        return j;
      } catch (e) { if (e.final) throw e; err = e; await sleep(400 * (i + 1)); }
    }
    throw new Error(`${method} ${url} failed after retries: ${err?.cause?.code || err?.message}`);
  };
  return { get: (u, k, o) => call('GET', u, k, undefined, o), post: (u, k, b, o) => call('POST', u, k, b ?? {}, o),
    put: (u, k, b, o) => call('PUT', u, k, b, o), del: (u, k, o) => call('DELETE', u, k, undefined, o), call };
}

export function tmpDir(prefix) { return fs.mkdtempSync(path.join(os.tmpdir(), prefix)); }

/** Tiny static server for exactly one file (the training footage). */
export function serveFile(file, port) {
  const name = path.basename(file);
  const srv = http.createServer((req, res) => {
    if (decodeURIComponent(req.url.split('?')[0]) !== '/' + name) { res.writeHead(404); return res.end(); }
    const st = fs.statSync(file);
    res.writeHead(200, { 'content-type': 'video/mp4', 'content-length': st.size, 'accept-ranges': 'none' });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((r) => srv.listen(port, '127.0.0.1', () => r({ srv, url: `http://127.0.0.1:${port}/${encodeURIComponent(name)}` })));
}

export function tts(text, voice, out, speed = 1.0, tail = 1.2) {
  const so = execFileSync(PY, [path.join(E2E_DIR, 'tts.py'), text, voice, out, String(speed), String(tail)], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
  return parseFloat(so.trim().split('\n').pop());
}

export class Report {
  constructor() { this.rows = []; }
  async step(name, fn, { skip } = {}) {
    if (skip) { this.rows.push({ name, status: 'SKIP', note: skip }); console.log(`SKIP  ${name}  (${skip})`); return false; }
    const t0 = Date.now(); process.stdout.write(`...   ${name}\n`);
    try {
      const note = await fn();
      this.rows.push({ name, status: 'PASS', ms: Date.now() - t0, note: note || '' });
      console.log(`PASS  ${name}  [${((Date.now() - t0) / 1000).toFixed(1)}s]${note ? '  ' + note : ''}`);
      return true;
    } catch (e) {
      this.rows.push({ name, status: 'FAIL', ms: Date.now() - t0, note: e.message });
      console.log(`FAIL  ${name}  [${((Date.now() - t0) / 1000).toFixed(1)}s]\n      ${String(e.message).split('\n').join('\n      ')}`);
      this.lastError = e;
      return false;
    }
  }
  get failed() { return this.rows.filter((r) => r.status === 'FAIL'); }
}
export function check(cond, msg) { if (!cond) throw new Error(msg); }
