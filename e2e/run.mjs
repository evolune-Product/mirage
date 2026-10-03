// Mirage end-to-end suite. `make e2e` (full) or `make e2e-smoke`.
// Starts ISOLATED backend / web / lipsync / worker on free ports with a temp DB + data dir. Never touches :8000/:8100/:3000.
import { chromium } from 'playwright';
import crypto from 'node:crypto';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import * as H from './lib/harness.mjs';
import { smokeRoutes } from './lib/routes.mjs';

const { ROOT, ART, sleep, until, check } = H;
const args = process.argv.slice(2);
const SMOKE = args.includes('--smoke');
const KEEP = args.includes('--keep');
const NOVIDEO = args.includes('--skip-video');
const ONLY = (args.find((a) => a.startsWith('--only=')) || '').slice(7);
const FOOTAGE = process.env.E2E_FOOTAGE || path.join(os.homedir(), 'Desktop/SpendVeto Video and PPT/SpendVeto_Founder_Video_1min.mp4');
const VIDEO_TIMEOUT = Number(process.env.E2E_VIDEO_TIMEOUT_S || 600) * 1000;

fs.rmSync(ART, { recursive: true, force: true });
const LOGS = path.join(ART, 'logs'); fs.mkdirSync(LOGS, { recursive: true });
const work = H.tmpDir('mirage-e2e-');
const DATA = path.join(work, 'data'); fs.mkdirSync(DATA);
const ports = { api: await H.freePort(), web: await H.freePort(), lip: await H.freePort(), vid: await H.freePort(), hook: await H.freePort() };
const API = `http://127.0.0.1:${ports.api}`, WEB = `http://127.0.0.1:${ports.web}`, LIP = `http://127.0.0.1:${ports.lip}`;
const api = H.apiClient(API);
const R = new H.Report();
const pages = []; // every page we open, for failure screenshots
const S = {}; // shared state between steps
const BROWSER_ARGS = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--use-fake-ui-for-media-stream',
  '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'];

console.log(`Mirage e2e (${SMOKE ? 'smoke' : 'full'})  api=${ports.api} web=${ports.web} lipsync=${ports.lip}  work=${work}`);
process.on('SIGINT', () => { H.stopAll(); process.exit(130); });

// ---------- services ----------
const env = { MIRAGE_DB_URL: `sqlite:///${work}/e2e.db`, MIRAGE_DATA: DATA, MIRAGE_CORS_ORIGINS: WEB, MIRAGE_PRELOAD: SMOKE ? '0' : '1',
  MIRAGE_LIPSYNC_URL: LIP, MIRAGE_ENV: 'dev', MIRAGE_RENDER_FPS: process.env.MIRAGE_RENDER_FPS || '8' };
H.start('backend', path.join(ROOT, 'backend/.venv/bin/uvicorn'), ['app.main:app', '--host', '127.0.0.1', '--port', String(ports.api)], { cwd: path.join(ROOT, 'backend'), env, logDir: LOGS });
H.start('web', 'npm', ['run', 'dev', '--', '-p', String(ports.web)], { cwd: path.join(ROOT, 'web'), env: { NEXT_DIST: '.next-e2e', NEXT_PUBLIC_API_URL: API, NEXT_TELEMETRY_DISABLED: '1' }, logDir: LOGS });
if (!SMOKE) {
  const venv = fs.existsSync(path.join(ROOT, 'workers/.venv-face/bin/uvicorn')) ? '.venv-face' : '.venv';
  H.start('lipsync', path.join(ROOT, `workers/${venv}/bin/uvicorn`), ['lipsync_server:app', '--host', '127.0.0.1', '--port', String(ports.lip)],
    { cwd: path.join(ROOT, 'workers'), env: { PYTORCH_ENABLE_MPS_FALLBACK: '1', MIRAGE_DATA: DATA }, logDir: LOGS });
  H.start('worker', H.PY, [path.join(ROOT, 'workers/run_worker.py'), '--poll', '1'], { cwd: path.join(ROOT, 'backend'), env, logDir: LOGS });
}

// webhook receiver: records every delivery (path, headers, raw body); /fail returns 500
const hooks = [];
const hookSrv = http.createServer((req, res) => {
  let b = ''; req.on('data', (c) => (b += c)); req.on('end', () => {
    hooks.push({ path: req.url, headers: req.headers, body: b, at: Date.now() });
    res.writeHead(req.url === '/fail' ? 500 : 200); res.end('ok');
  });
});
await new Promise((r) => hookSrv.listen(ports.hook, '127.0.0.1', r));
let footage = null;
if (!SMOKE) {
  check(fs.existsSync(FOOTAGE), `seed footage not found at ${FOOTAGE}; set E2E_FOOTAGE=/path/to/face_video.mp4`);
  footage = await H.serveFile(FOOTAGE, ports.vid);
}

async function shutdown(code) {
  hookSrv.close(); footage?.srv.close();
  if (KEEP) { console.log(`--keep: services left running (api ${API}, web ${WEB}); kill them manually`); process.exit(code); }
  H.stopAll(); await sleep(500); process.exit(code);
}

async function failShots(stepName) {
  let i = 0;
  for (const p of pages) {
    if (p.isClosed()) continue;
    try { await p.screenshot({ path: path.join(ART, `FAIL-${stepName.replace(/\W+/g, '_')}-${i++}.png`), fullPage: false, timeout: 8000 }); } catch {}
  }
}
const step = async (name, fn, opt) => {
  if (ONLY && !name.toLowerCase().includes(ONLY.toLowerCase())) return false;
  const ok = await R.step(name, fn, opt);
  if (!ok && !opt?.skip) await failShots(name);
  return ok;
};
const newPage = async (browser, w, h, errs) => {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, permissions: ['microphone'] });
  const p = await ctx.newPage(); pages.push(p);
  if (errs) {
    p.on('pageerror', (e) => errs.push('pageerror: ' + e.message.slice(0, 200)));
    p.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });
  }
  return { ctx, p };
};

try {
  await step('services ready', async () => {
    await Promise.all([
      H.waitHttp(`${API}/health`, { name: 'backend', timeout: 90000, ok: (r) => r.status === 200 }),
      H.waitHttp(`${WEB}/signup`, { name: 'web dashboard (first compile)', timeout: 240000 }),
      ...(SMOKE ? [] : [H.waitHttp(`${LIP}/health`, { name: 'lipsync', timeout: 120000, ok: (r) => r.status === 200 })]),
    ]);
  });
  if (R.failed.length) throw new Error('required services did not start; aborting (see e2e/artifacts/logs/*.log)');

  let ollamaOk = false;
  if (!SMOKE) {
    try {
      const t = await (await fetch(`${process.env.OLLAMA_URL || 'http://localhost:11434'}/api/tags`)).json();
      ollamaOk = t.models.some((m) => m.name.startsWith('llama3.2:1b'));
    } catch {}
  }
  const needLive = SMOKE ? 'smoke mode' : !ollamaOk ? 'Ollama / llama3.2:1b not available' : false;

  // ---------- 1. signup ----------
  const browser = await chromium.launch({ args: BROWSER_ARGS });
  const uiErrs = [];
  let ui; // consent/UI browser context + page
  await step('signup via dashboard UI', async () => {
    ui = await newPage(browser, 1440, 900, uiErrs);
    await ui.p.goto(WEB + '/signup');
    await ui.p.fill('input[type=email]', `e2e${Date.now()}@example.com`);
    await ui.p.click('button:has-text("Sign up")');
    await ui.p.waitForURL('**/dashboard', { timeout: 30000 });
    S.key = await ui.p.evaluate(() => Object.values(localStorage).find((v) => String(v).startsWith('mk_')));
    check(S.key, 'no api key stored in localStorage after signup');
    const st = await api.get('/v1/usage', S.key); check(st.credits_seconds > 0, 'new account has no credits');
    return `credits=${st.credits_seconds}s`;
  });
  const key = () => { check(S.key, 'blocked: signup failed earlier'); return S.key; };
  const need = (v, what) => check(v, `blocked: ${what} failed earlier`);

  // webhook endpoint first, so every later event is delivered to the local receiver
  await step('webhook endpoint registered', async () => {
    const ep = await api.post('/v1/webhooks', key(), { url: `http://127.0.0.1:${ports.hook}/hook`, events: ['*'] });
    S.hookSecret = ep.secret; check(S.hookSecret?.startsWith('whsec_'), 'no signing secret returned');
    S.hookId = ep.id;
  });

  // ---------- 2. replica + consent (fake mic = Kokoro speech) ----------
  const FAKEMIC = path.join(work, 'fakemic.wav');
  await step('replica + voice consent (fake mic) + worker training', async () => {
    const mic = await chromium.launch({ args: [...BROWSER_ARGS, '--use-file-for-fake-audio-capture=' + FAKEMIC] });
    H.tts('Please wait for the consent phrase.', 'af_heart', FAKEMIC);
    try {
      const { ctx, p } = await newPage(mic, 1440, 900, uiErrs);
      await ctx.addInitScript((k) => localStorage.setItem('mirage_api_key', k), key());
      await p.goto(WEB + '/dashboard/replicas');
      await p.click('button:has-text("New replica")');
      await p.fill('input[placeholder="e.g. Founder"]', 'Founder');
      await p.fill('input[type=url]', footage.url);
      await p.click('form button:has-text("Create replica")');
      await p.waitForSelector('text=awaiting consent', { timeout: 20000 });
      await p.click('button:has-text("Give consent")');
      await p.waitForSelector('[data-testid=consent-phrase]');
      const phrase = (await p.locator('[data-testid=consent-phrase]').innerText()).trim();
      await p.locator('input').nth(0).fill('Founder');
      // Kokoro/Whisper can confuse look-alike code words ("amber"/"ember"): retry with other voices, slower, code words as separate sentences
      const spoken = phrase.replace(/(code is) ([^.]*)\./i, (_, a, codes) => `${a} ${codes.split(',').map((w) => w.trim()).join('. ')}.`);
      let accepted = false, lastErr = '';
      for (const [i, voice] of ['af_heart', 'af_bella', 'af_sarah', 'am_michael'].entries()) {
        const dur = H.tts(spoken, voice, FAKEMIC, i === 0 ? 1.0 : 0.85); // chromium reads the file when the stream opens
        await p.click('[data-testid=consent-record]'); await sleep((dur + 0.3) * 1000);
        await p.click('[data-testid=consent-stop]'); await sleep(500);
        await p.click('[data-testid=consent-submit]');
        const res = await Promise.race([
          p.waitForSelector('text=Waiting for a worker', { timeout: 120000 }).then(() => 'ok'),
          p.waitForSelector('[data-testid=consent-error]', { timeout: 120000 }).then((el) => el.innerText()),
        ]);
        if (res === 'ok') { accepted = true; S.consentAttempts = i + 1; break; }
        lastErr = res; console.log(`      consent attempt ${i + 1} (${voice}) rejected, retrying: ${res.replace(/\n/g, ' ').slice(0, 160)}`);
        await p.click('button:has-text("Re-record")');
      }
      check(accepted, 'consent rejected after 4 attempts: ' + lastErr);
      const reps = await api.get('/v1/replicas', key()); S.replicaId = reps[0].id;
      const c = await api.get(`/v1/replicas/${S.replicaId}/consent`, key());
      check(c.records?.length === 1, 'no consent record stored');
      S.consentVerified = c.records[0].verified_by;
      await until(async () => (await api.get(`/v1/replicas/${S.replicaId}`, key())).status === 'ready', { timeout: 240000, every: 2000, what: 'worker to mark replica ready' });
      return `consent verified_by=${S.consentVerified}`;
    } finally { await mic.close(); }
  }, { skip: SMOKE && 'smoke mode (needs Kokoro + worker)' });

  // ---------- 3. persona + knowledge (UI) ----------
  await step('persona + knowledge document', async () => {
    if (SMOKE) {
      const pe = await api.post('/v1/personas', key(), { name: 'Sales Rep', system_prompt: 'You are a friendly sales rep for Mirage.' }); S.personaId = pe.id;
      await api.post(`/v1/personas/${pe.id}/knowledge/text`, key(), { title: 'pricing', text: 'Mirage Starter plan costs 19 dollars per month and includes 120 minutes.' });
      return 'via API (smoke)';
    }
    need(S.replicaId, 'replica step');
    const p = ui.p;
    await p.goto(WEB + '/dashboard/personas'); await p.click('button:has-text("New persona")');
    await p.locator('[role=dialog] input').nth(0).fill('Sales Rep');
    await p.locator('[role=dialog] select').selectOption({ index: 1 });
    await p.locator('[role=dialog] textarea').nth(0).fill('You are a friendly sales rep for Mirage. Answer from the documents in one short sentence.');
    await p.click('[role=dialog] button:has-text("Create persona")');
    await p.fill('input[placeholder^="Title"]', 'pricing');
    await p.fill('textarea[placeholder^="Paste"]', 'Mirage Starter plan costs 19 dollars per month and includes 120 minutes. Pro costs 79 dollars per month.');
    await p.click('button:has-text("Add document")');
    await p.waitForFunction(() => document.querySelector('[role=dialog]')?.innerText.includes('pricing'), null, { timeout: 30000 });
    const ps = await api.get('/v1/personas', key()); S.personaId = ps[0].id;
    check(ps[0].replica_id === S.replicaId, 'persona not linked to the replica');
  });

  // ---------- 4. live conversation (fake mic -> STT -> LLM -> TTS -> lip-sync frames) ----------
  async function liveConversation(label, urlFor, question) {
    const qwav = path.join(work, `q_${label}.wav`);
    H.tts(question, 'af_heart', qwav, 1.0, 90);
    const lb = await chromium.launch({ args: [...BROWSER_ARGS, '--use-file-for-fake-audio-capture=' + qwav] });
    const errs = [];
    try {
      const { p } = await newPage(lb, 1000, 1000, errs);
      await p.goto(urlFor); await p.waitForSelector('#go', { timeout: 20000 });
      await p.click('#go');
      let L;
      try {
        L = await until(async () => {
          const o = await p.evaluate(() => ({ ...window.__live, log: document.getElementById('log').innerText }));
          return /Agent:/.test(o.log) && o.speakingFrames > 10 ? o : false;
        }, { timeout: 150000, every: 400, what: 'agent answer + lip-sync frames' });
      } catch (e) {
        const o = await p.evaluate(() => ({ ...window.__live, log: document.getElementById('log')?.innerText, state: document.getElementById('state')?.innerText })).catch(() => ({}));
        await p.screenshot({ path: path.join(ART, `FAIL-live-${label}.png`) }).catch(() => {});
        throw new Error(`${e.message}\n  page state: ${JSON.stringify(o).slice(0, 600)}\n  browser errors: ${errs.join(' | ') || 'none'}`);
      }
      const lat = L.tFirst && L.tUser ? Math.round(L.tFirst - L.tUser) : null;
      check(L.idleFrames > 0, 'idle loop never drawn');
      check(L.segments > 0, 'no lip-sync video_segment received');
      check(!errs.length, 'browser errors: ' + errs.join(' | '));
      return { L, lat, log: L.log };
    } finally { await lb.close(); }
  }
  await step('live conversation: agent answers + lip-sync frames', async () => {
    need(S.personaId, 'persona step');
    const cv = await api.post('/v1/conversations', key(), { persona_id: S.personaId }); S.cid = cv.id;
    const r = await liveConversation('live', `${API}/static/playground.html?cid=${cv.id}&api_key=${key()}`, 'How much does the Starter plan cost per month?');
    check(/19|nineteen/i.test(r.log), 'agent answer did not use the knowledge document ($19):\n' + r.log);
    await api.post(`/v1/conversations/${cv.id}/end`, key());
    const tr = await api.get(`/v1/conversations/${cv.id}/transcript`, key());
    check((tr.turns || []).length >= 2, 'transcript has < 2 turns');
    console.log(`      latency user-transcript -> first lip-synced frame: ${r.lat} ms | segments=${r.L.segments} speakingFrames=${r.L.speakingFrames} idleFrames=${r.L.idleFrames}`);
    S.latency = r.lat;
    return `first-frame latency ${r.lat} ms, ${r.L.speakingFrames} speaking frames`;
  }, { skip: needLive });

  // ---------- 5. video generation (queued now, awaited later: LivePortrait is slow on a Mac) ----------
  await step('video generation submitted', async () => {
    need(S.replicaId, 'replica step');
    const v = await api.post('/v1/videos', key(), { replica_id: S.replicaId, script: 'Hello from Mirage.' }); S.videoId = v.id;
    return v.id;
  }, { skip: (SMOKE && 'smoke mode') || (NOVIDEO && '--skip-video') });

  // ---------- 6. dashboard + site smoke ----------
  const routes = smokeRoutes(path.join(ROOT, 'web/app'), SMOKE);
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    await step(`dashboard/site smoke @${w} (${routes.length} routes: no console errors, no horizontal overflow)`, async () => {
      const errs = [], bad = [];
      const { ctx, p } = await newPage(browser, w, h, errs);
      await ctx.addInitScript((k) => localStorage.setItem('mirage_api_key', k), key());
      p.on('response', (r) => { if (r.status() >= 400 && r.url().startsWith(API)) errs.push(`http ${r.status()} ${r.url().replace(API, '')}`); });
      for (const route of routes) {
        errs.length = 0;
        try { await p.goto(WEB + route, { waitUntil: 'domcontentloaded', timeout: 120000 }); } catch (e) { bad.push(`${route}: navigation failed ${e.message.slice(0, 80)}`); continue; }
        await p.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {}); await sleep(600);
        const ov = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        if (ov > 0) {
          const who = await p.evaluate(() => [...document.querySelectorAll('body *')].filter((e) => e.getBoundingClientRect().right > window.innerWidth + 1)
            .slice(0, 3).map((e) => `${e.tagName.toLowerCase()}.${String(e.className).slice(0, 60)} "${(e.textContent || '').trim().slice(0, 40)}"`));
          bad.push(`${route}: horizontal overflow ${ov}px; wide elements: ${who.join(' | ')}`);
        }
        const real = errs.filter((e) => !/favicon|404 \(Not Found\).*\.(png|ico)/.test(e));
        if (real.length) bad.push(`${route}: ${[...new Set(real)].slice(0, 3).join(' | ')}`);
        if (process.env.E2E_SHOTS) await p.screenshot({ path: path.join(ART, `smoke-${w}-${route.replace(/\W+/g, '_') || 'home'}.png`), fullPage: true });
      }
      check(!bad.length, bad.join('\n'));
      await ctx.close();
      return `${routes.length} routes ok`;
    });
  }

  // ---------- 7. guest link ----------
  await step('guest link: info, page, conversation, revoke', async () => {
    need(S.personaId, 'persona step');
    const sh = await api.post(`/v1/personas/${S.personaId}/share`, key(), { label: 'e2e', max_seconds: 60, max_total_seconds: 300 });
    check(/\/guest\/sh_/.test(sh.url), 'share url malformed: ' + sh.url);
    const info = await api.get(`/v1/guest/${sh.token}/info`, null); check(info.available, 'guest link not available');
    const page = await fetch(`${API}/guest/${sh.token}`); check(page.status === 200 && (await page.text()).includes('mirage-client.js'), 'guest page did not load');
    let note = 'page + info ok';
    if (!needLive) {
      const gcv = { id: '' };
      const r = await liveConversation('guest', `${API}/guest/${sh.token}`, 'How much does the Starter plan cost per month?');
      check(/19|nineteen/i.test(r.log), 'guest agent answer did not use the knowledge document ($19):\n' + r.log);
      note = `live guest conversation ok (${r.lat} ms)`;
    }
    await api.del(`/v1/share/${sh.token}`, key());
    const after = await api.get(`/v1/guest/${sh.token}/info`, null, { raw: true }); check(after.status === 404, 'revoked link still works');
    return note;
  });

  // ---------- 8. video result ----------
  await step('video renders, downloads as a valid mp4', async () => {
    need(S.videoId, 'video submission');
    const v = await until(async () => {
      const x = await api.get(`/v1/videos/${S.videoId}`, key());
      if (x.status === 'error') throw Object.assign(new Error('video job errored'), { final: true });
      return x.status === 'ready' ? x : false;
    }, { timeout: VIDEO_TIMEOUT, every: 3000, what: 'video to render (see logs/worker.log)' });
    const r = await fetch(v.output_url.startsWith('http') ? v.output_url : API + v.output_url);
    check(r.status === 200, `video download -> ${r.status}`);
    const buf = Buffer.from(await r.arrayBuffer()); check(buf.length > 2000, 'video file is tiny');
    check(buf.slice(4, 8).toString() === 'ftyp', 'not an mp4 (no ftyp box)');
    const f = path.join(work, 'out.mp4'); fs.writeFileSync(f, buf);
    const dur = parseFloat(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f], { encoding: 'utf8' }));
    check(dur > 0.5, `video duration ${dur}s`);
    return `${(buf.length / 1024).toFixed(0)} KB, ${dur.toFixed(1)} s`;
  }, { skip: (SMOKE && 'smoke mode') || (NOVIDEO && '--skip-video') });

  // ---------- 9. webhooks ----------
  await step('webhooks: signed deliveries reach the receiver', async () => {
    const t = await api.post(`/v1/webhooks/${S.hookId}/test`, key()); check(t.status === 'delivered', 'test delivery status ' + t.status);
    const need = SMOKE ? ['webhook.test'] : ['webhook.test', 'conversation.started', 'conversation.ended', 'replica.ready', ...(NOVIDEO ? [] : ['video.ready'])];
    const seen = () => hooks.map((h) => { try { return JSON.parse(h.body).type; } catch { return '?'; } });
    await until(() => need.every((e) => seen().includes(e)), { timeout: 60000, what: `events ${need.join(', ')} (got ${[...new Set(seen())].join(', ')})` });
    for (const h of hooks.filter((x) => x.path === '/hook')) {
      const sig = h.headers['mirage-signature'] || ''; const m = /t=(\d+),v1=([0-9a-f]+)/.exec(sig); check(m, 'missing/malformed Mirage-Signature');
      const mac = crypto.createHmac('sha256', S.hookSecret).update(`${m[1]}.${h.body}`).digest('hex');
      check(crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(m[2])), 'HMAC signature does not verify');
    }
    // retry path: an endpoint that answers 500 records a failed attempt with a scheduled retry
    const bad = await api.post('/v1/webhooks', key(), { url: `http://127.0.0.1:${ports.hook}/fail`, events: ['*'] });
    const t2 = await api.post(`/v1/webhooks/${bad.id}/test`, key());
    check(t2.status !== 'delivered' && t2.last_status_code === 500, `failing endpoint: status=${t2.status} code=${t2.last_status_code}`);
    return `${hooks.length} deliveries, signatures verified, retry scheduled on 500`;
  });

  // ---------- 10. metrics/health (platform) ----------
  await step('health endpoint', async () => { const h = await api.get('/health', null); check(h.ok === true, 'health not ok'); return JSON.stringify(h).slice(0, 120); });

  await browser.close();
  if (uiErrs.length) console.log('UI console errors during flows (informational):\n  ' + [...new Set(uiErrs)].slice(0, 8).join('\n  '));
} catch (e) {
  console.log('ABORTED:', e.message);
  R.rows.push({ name: 'suite', status: 'FAIL', note: e.message });
}

console.log('\n==== e2e summary ====');
for (const r of R.rows) console.log(`${r.status.padEnd(5)} ${r.name}${r.note && r.status !== 'PASS' ? '  -> ' + String(r.note).split('\n')[0] : ''}`);
const failed = R.rows.filter((r) => r.status === 'FAIL').length;
fs.writeFileSync(path.join(ART, 'summary.json'), JSON.stringify({ at: new Date().toISOString(), smoke: SMOKE, rows: R.rows }, null, 2));
console.log(failed ? `\n${failed} FAILED. Logs + screenshots: ${ART}` : `\nALL GREEN (${R.rows.filter((r) => r.status === 'PASS').length} passed, ${R.rows.filter((r) => r.status === 'SKIP').length} skipped)`);
await shutdown(failed ? 1 : 0);
