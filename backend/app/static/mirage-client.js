/* Mirage browser client, shared by playground.html and guest.html.
 *
 *   const c = new MirageClient({ wsUrl: () => 'ws://...', prepare: async () => {...} });   // transport + audio + video
 *   MirageUI.mount(document.getElementById('mirage'), c, { mode: 'playground' | 'guest', ... });  // controls + transcript
 *
 * Protocol additions are documented in docs/overnight/realtime-client.md. A server that does not know `hello` simply
 * ignores it and keeps speaking the original protocol (raw PCM, base64 JPEG in JSON); this client handles both.
 */
(() => {
'use strict';
const AC = window.AudioContext || window.webkitAudioContext;
const VERSION = '2';

/* ---- human messages -------------------------------------------------------------------------------------------- */
const MSG = {
  micDenied: 'Microphone access is blocked. Allow it from the lock icon in the address bar, then press Start again.',
  micNone: 'No microphone found. Plug one in or check your system sound settings, then press Start again.',
  micBusy: 'Your microphone is being used by another app. Close it and press Start again.',
  micInsecure: 'The microphone only works on HTTPS pages (or localhost). Open this page over HTTPS.',
  micOther: 'Could not open the microphone: ',
  noAudio: 'This browser cannot capture audio (no Web Audio support).',
  4401: 'This API key is not valid.',
  4404: 'This conversation or link was not found (or it has expired).',
  4402: 'This conversation has ended, or the account is out of credits.',
  4408: 'Time limit reached - thanks for talking!',
  4409: 'This conversation was taken over by another tab or device.',
  1013: 'The server is busy right now. Please try again in a moment.',
  unreachable: 'Could not reach the server. Check your connection and press Start to try again.',
  gaveUp: 'Could not reconnect. Press Start to begin again.',
  ended: 'Conversation ended.',
};
const TERMINAL = new Set([4401, 4402, 4404, 4408, 4409]);
const BACKOFF = [0.4, 1, 2, 4, 6, 8, 8];  // seconds; about 30 s in total, which is the server's resume grace period

/* ---- audio capture helpers (browser-side resampling when the context cannot run at 16 kHz) ---------------------- */
const RESAMPLER_SRC = `
function makeResampler(inRate, outRate) {
  const step = inRate / outRate; let buf = new Float32Array(0), pos = 0;
  return function (x) {
    if (step === 1) return x;
    const b = new Float32Array(buf.length + x.length); b.set(buf); b.set(x, buf.length); buf = b;
    const out = [];
    while (pos + step <= buf.length) {
      const a = pos, e = pos + step; let s = 0, i = Math.floor(a);
      while (i < e && i < buf.length) { const lo = Math.max(a, i), hi = Math.min(e, i + 1); s += buf[i] * (hi - lo); i++; }
      out.push(s / step); pos += step;
    }
    const drop = Math.floor(pos); buf = buf.slice(drop); pos -= drop;
    return Float32Array.from(out);
  };
}`;
const WORKLET_SRC = RESAMPLER_SRC + `
class P extends AudioWorkletProcessor {
  constructor() { super(); this.rs = makeResampler(sampleRate, 16000); this.b = new Int16Array(320); this.n = 0; this.ss = 0; this.sn = 0; }
  process(inputs) {
    const c = inputs[0] && inputs[0][0]; if (!c) return true;
    const y = this.rs(c);
    for (let k = 0; k < y.length; k++) {
      const v = Math.max(-1, Math.min(1, y[k])); this.ss += v * v; this.sn++;
      this.b[this.n++] = v < 0 ? v * 32768 : v * 32767;
      if (this.n === 320) { const o = this.b.slice(); this.port.postMessage(o.buffer, [o.buffer]); this.n = 0; }
    }
    if (this.sn >= 1600) { this.port.postMessage({ level: Math.sqrt(this.ss / this.sn) }); this.ss = 0; this.sn = 0; }
    return true;
  }
}
registerProcessor('pcm16', P);`;
// eslint-disable-next-line no-new-func
const makeResampler = new Function(RESAMPLER_SRC + '; return makeResampler;')();

const rms16 = (i16) => { let s = 0; for (let i = 0; i < i16.length; i++) s += i16[i] * i16[i]; return Math.sqrt(s / Math.max(1, i16.length)) / 32768; };
const isMobile = () => /iPhone|iPad|iPod|Android/i.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && /Mac/.test(navigator.platform));

class MirageClient {
  constructor(o) {
    this.o = o || {};
    this.h = {};
    this.state = 'idle';
    this.LIVE = window.__live = { segments: 0, speakingFrames: 0, idleFrames: 0, decodeMs: 0, tUser: 0, tFirst: 0 };
    this.stats = { rxBytes: 0, rxVideoBytes: 0, rxAudioBytes: 0, rxMsgs: 0, rtt: 0, lagMax: 0, decodeMs: 0, tier: null, reconnects: 0,
                   inRate: 0, resampled: false, micFrames: 0, micFramesSent: 0 };
    this.muted = false; this.ptt = false; this.pttDown = false;
    this.micLevel = 0; this.audioSettings = {};
    this.shares = {};
    this.log = [];  // transcript: {t, role, text}
    this.reset();
  }
  reset() {
    this.idleImgs = []; this.idleFps = 25; this.segs = []; this.pendingSeg = null; this.vctx = null;
    this.anchor = { t: 0, k: 0 }; this.lastEnd = null; this.wasSpeaking = false;
    this.nextT = 0; this.sources = []; this.agentDone = true;
  }
  on(n, f) { (this.h[n] = this.h[n] || []).push(f); return this; }
  emit(n, d) { (this.h[n] || []).forEach((f) => { try { f(d); } catch (e) { console.error(e); } }); }
  setState(s, text) { this.state = s; this.emit('state', { state: s, text: text || s }); }

  /* ---- start / stop ----------------------------------------------------------------------------------------- */
  async start() {
    if (this.running) return;
    this.running = true; this.stopping = false; this.everReady = false; this.attempt = 0; this.t0 = Date.now(); this.log = [];
    this.setState('connecting', 'connecting models...');
    try {
      if (this.o.prepare) await this.o.prepare();
      await this._openAudio();
    } catch (e) { return this._fail(e.userMessage || (e && e.message) || String(e)); }
    this._connect();
    this._timers();
  }
  _fail(text) {
    this._teardown();
    this.running = false;
    this.setState('error', text);
    this.emit('fatal', { text });
  }
  stop(reason) {
    if (!this.running) return;
    this.stopping = true;
    try { if (this.ws && this.ws.readyState === 1) { this.ws.send(JSON.stringify({ type: 'end' })); } } catch (e) { /* closing anyway */ }
    try { this.ws && this.ws.close(1000, 'client ended'); } catch (e) { /* ignore */ }
    this._finish(reason || MSG.ended, 'ended');
  }
  _finish(text, state) {
    this._teardown();
    this.running = false;
    this.setState(state || 'ended', text);
    this.emit('ended', { text });
  }
  _teardown() {
    clearInterval(this._hb); clearInterval(this._tickT); clearInterval(this._statT); clearTimeout(this._rcT);
    try { this.micNode && this.micNode.disconnect(); } catch (e) { /* ignore */ }
    try { this.proc && this.proc.disconnect(); } catch (e) { /* ignore */ }
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    ['camera', 'screen'].forEach((k) => this.stopShare(k, true));
    try { this.inCtx && this.inCtx.close(); } catch (e) { /* ignore */ }
    try { this.outCtx && this.outCtx.close(); } catch (e) { /* ignore */ }
    this.stream = this.inCtx = this.outCtx = this.micNode = this.proc = null;
    this.ws = null;
    this.stopPlayback();
    navigator.mediaDevices && (navigator.mediaDevices.ondevicechange = null);
    document.removeEventListener('visibilitychange', this._vis); window.removeEventListener('online', this._vis);
    if (this._hide) window.removeEventListener('pagehide', this._hide);
  }

  /* ---- microphone + audio graph ------------------------------------------------------------------------------ */
  async _getMic() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      const e = new Error(MSG.micInsecure); e.userMessage = window.isSecureContext ? MSG.noAudio : MSG.micInsecure; throw e;
    }
    // Ask for the browser's own echo cancellation / noise suppression / AGC; report what was actually granted.
    const want = { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 };
    try {
      return await navigator.mediaDevices.getUserMedia({ audio: want });
    } catch (e) {
      const n = e && e.name; const err = new Error(e.message);
      if (n === 'NotAllowedError' || n === 'PermissionDeniedError' || n === 'SecurityError') err.userMessage = MSG.micDenied;
      else if (n === 'NotFoundError' || n === 'DevicesNotFoundError' || n === 'OverconstrainedError') err.userMessage = MSG.micNone;
      else if (n === 'NotReadableError' || n === 'TrackStartError' || n === 'AbortError') err.userMessage = MSG.micBusy;
      else err.userMessage = MSG.micOther + (e.message || n);
      throw err;
    }
  }
  async _openAudio() {
    if (!AC) { const e = new Error(MSG.noAudio); e.userMessage = MSG.noAudio; throw e; }
    this.stream = await this._getMic();
    this._readSettings();
    // Output: 24 kHz context when allowed (no resampling of the agent's PCM); otherwise the device rate (the browser
    // resamples AudioBuffers on playback, which every engine supports).
    try { this.outCtx = new AC({ sampleRate: 24000 }); } catch (e) { this.outCtx = new AC(); }
    this.outGain = this.outCtx.createGain();
    this.analyser = this.outCtx.createAnalyser(); this.analyser.fftSize = 1024;
    this.outGain.connect(this.analyser); this.analyser.connect(this.outCtx.destination);
    this.outCtx.onstatechange = () => this._audioState();
    try { await this.outCtx.resume(); } catch (e) { /* autoplay policy: the UI offers a tap-to-enable button */ }
    await this._buildMicGraph();
    this._audioState();
    if (navigator.mediaDevices.addEventListener) navigator.mediaDevices.ondevicechange = () => this._deviceChange();
    this.stream.getAudioTracks().forEach((t) => { t.onended = () => this._micEnded(); });
    this._detectHeadphones();
  }
  _readSettings() {
    const t = this.stream.getAudioTracks()[0]; let s = {};
    try { s = t.getSettings ? t.getSettings() : {}; } catch (e) { /* ignore */ }
    this.audioSettings = { echoCancellation: s.echoCancellation, noiseSuppression: s.noiseSuppression, autoGainControl: s.autoGainControl,
                           sampleRate: s.sampleRate, label: (t.label || '').slice(0, 60) };
    this.emit('audio_settings', this.audioSettings);
    if (s.echoCancellation === false) this.emit('echo', { suspected: true, reason: 'aec-off' });
  }
  async _buildMicGraph() {
    // Prefer a 16 kHz context (no JS resampling). Safari/Firefox may refuse or silently use the device rate: then the
    // worklet resamples (area averaging, 48000/44100 -> 16000). AudioWorklet missing: ScriptProcessor fallback.
    let ctx = null, src = null;
    try {
      ctx = new AC({ sampleRate: 16000 });
      src = ctx.createMediaStreamSource(this.stream);
    } catch (e) {
      try { ctx && ctx.close(); } catch (e2) { /* ignore */ }
      ctx = new AC(); src = ctx.createMediaStreamSource(this.stream);
    }
    this.inCtx = ctx; this.stats.inRate = ctx.sampleRate; this.stats.resampled = ctx.sampleRate !== 16000;
    try { await ctx.resume(); } catch (e) { /* see _audioState */ }
    let worklet = false;
    if (ctx.audioWorklet && window.AudioWorkletNode) {
      try {
        const url = URL.createObjectURL(new Blob([WORKLET_SRC], { type: 'text/javascript' }));
        await ctx.audioWorklet.addModule(url);
        const node = new AudioWorkletNode(ctx, 'pcm16', { numberOfInputs: 1, numberOfOutputs: 0 });
        node.port.onmessage = (e) => (e.data instanceof ArrayBuffer ? this._micFrame(e.data) : this._micLevel(e.data.level));
        src.connect(node); this.micNode = node; worklet = true;
      } catch (e) { console.warn('AudioWorklet unavailable, using ScriptProcessor', e); }
    }
    if (!worklet) {
      const rs = makeResampler(ctx.sampleRate, 16000); let acc = new Int16Array(0);
      const p = ctx.createScriptProcessor(2048, 1, 1);
      p.onaudioprocess = (ev) => {
        const y = rs(ev.inputBuffer.getChannelData(0));
        const i16 = new Int16Array(y.length); for (let i = 0; i < y.length; i++) { const v = Math.max(-1, Math.min(1, y[i])); i16[i] = v < 0 ? v * 32768 : v * 32767; }
        const m = new Int16Array(acc.length + i16.length); m.set(acc); m.set(i16, acc.length); acc = m;
        let lv = 0;
        while (acc.length >= 320) { const f = acc.slice(0, 320); acc = acc.slice(320); lv = rms16(f); this._micFrame(f.buffer); }
        if (lv) this._micLevel(lv);
        ev.outputBuffer.getChannelData(0).fill(0);
      };
      const z = ctx.createGain(); z.gain.value = 0; src.connect(p); p.connect(z); z.connect(ctx.destination); this.proc = p;
    }
    this.stats.worklet = worklet;
  }
  _micLevel(l) { this.micLevel = this.muted ? 0 : l; }
  _micFrame(buf) {
    this.stats.micFrames++;
    const ws = this.ws;
    if (!this.ready || !ws || ws.readyState !== 1) return;
    if (this.muted || (this.ptt && !this.pttDown)) return;
    if (ws.bufferedAmount > 1e6) return;  // congested uplink: drop mic frames rather than add latency
    ws.send(buf); this.stats.micFramesSent++;
  }
  _audioState() {
    const bad = [this.outCtx, this.inCtx].some((c) => c && c.state !== 'running');
    this.emit('audio_blocked', { blocked: bad });
  }
  async resumeAudio() {
    for (const c of [this.outCtx, this.inCtx]) { try { c && (await c.resume()); } catch (e) { /* ignore */ } }
    this._audioState();
  }
  async _deviceChange() {
    this._detectHeadphones();
    const t = this.stream && this.stream.getAudioTracks()[0];
    if (t && t.readyState === 'ended') this._micEnded();
  }
  async _micEnded() {
    if (!this.running || this.stopping || this._reacq) return;
    this._reacq = true;
    this.emit('toast', { text: 'Microphone changed - reconnecting audio...' });
    try {
      try { this.micNode && this.micNode.disconnect(); } catch (e) { /* ignore */ }
      try { this.proc && this.proc.disconnect(); } catch (e) { /* ignore */ }
      try { this.inCtx && this.inCtx.close(); } catch (e) { /* ignore */ }
      this.stream && this.stream.getTracks().forEach((t) => t.stop());
      this.stream = await this._getMic();
      this._readSettings();
      await this._buildMicGraph();
      this.stream.getAudioTracks().forEach((t) => { t.onended = () => this._micEnded(); t.enabled = !this.muted; });
      this.emit('toast', { text: 'Microphone reconnected.' });
    } catch (e) {
      this.emit('toast', { text: e.userMessage || 'Microphone lost. Press Stop and Start to try again.', bad: true });
    } finally { this._reacq = false; }
  }
  async _detectHeadphones() {
    try {
      const d = await navigator.mediaDevices.enumerateDevices();
      const outs = d.filter((x) => x.kind === 'audiooutput');
      const label = (outs.find((x) => x.deviceId === 'default') || outs[0] || {}).label || '';
      this.headphones = /head(phone|set)|airpods|buds|earphone|earbud|bluetooth|wh-1000|bose|jabra/i.test(label);
      this.emit('headphones', { on: this.headphones, label });
    } catch (e) { /* enumerateDevices unavailable */ }
  }

  /* ---- mute / push-to-talk ----------------------------------------------------------------------------------- */
  setMuted(m) {
    this.muted = !!m;
    if (this.stream) this.stream.getAudioTracks().forEach((t) => { t.enabled = !this.muted; });
    if (this.muted) this.micLevel = 0;
    this.emit('muted', { muted: this.muted });
  }
  setPtt(on) {
    this.ptt = !!on; this.pttDown = false;
    this._send({ type: 'ptt', state: on ? 'on' : 'off' });
    this.emit('ptt', { on: this.ptt, down: false });
  }
  pttPress() {
    if (!this.ptt || this.pttDown || !this.ready) return;
    this.pttDown = true;
    this.stopPlayback();  // the user is talking: stop the agent immediately on this side too
    this._send({ type: 'ptt', state: 'down' });
    this.emit('ptt', { on: true, down: true });
  }
  pttRelease() {
    if (!this.ptt || !this.pttDown) return;
    this.pttDown = false;
    this.emit('ptt', { on: true, down: false });
    setTimeout(() => this._send({ type: 'ptt', state: 'up' }), 60);  // let the last mic frame (<= 20 ms + worklet hop) go first
  }

  /* ---- websocket ---------------------------------------------------------------------------------------------- */
  _send(o) { if (this.ws && this.ws.readyState === 1) { try { this.ws.send(JSON.stringify(o)); } catch (e) { /* closing */ } } }
  _connect() {
    let url = this.o.wsUrl();
    if (this.attempt > 0) url += (url.includes('?') ? '&' : '?') + 'resume=1';
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    this.ws = ws; this.ready = false; this.tagged = false; this.lastRx = performance.now();
    let q = Promise.resolve();
    ws.onopen = () => {
      if (this.attempt === 0) this.setState('connecting', 'connecting models...');
      const hello = { type: 'hello', v: 1, framing: 'tagged', ua: navigator.userAgent.slice(0, 120), mobile: isMobile(),
        audio: Object.assign({}, this.audioSettings, { ctxRate: this.stats.inRate, resampled: this.stats.resampled, worklet: this.stats.worklet }),
        idle_cached: this.idleImgs.length > 0, resume: this.attempt > 0 };
      const c = navigator.connection;
      if (isMobile() || (c && (c.saveData || /2g|3g/.test(c.effectiveType || '')))) hello.tier = 2;
      this._send(hello);
    };
    ws.onmessage = (ev) => { const t = performance.now(); this.lastRx = t; q = q.then(() => this._handle(ev, t)).catch((e) => console.error(e)); };
    ws.onerror = () => { /* onclose follows with the code */ };
    ws.onclose = (e) => { if (ws === this.ws) this._closed(e.code, e.reason); };
  }
  _closed(code, reason) {
    this.ready = false;
    if (this.stopping || !this.running) return;
    this.stopPlayback();
    if (TERMINAL.has(code)) {
      let text = MSG[code] || 'Disconnected.';
      if (code === 4402 && /credit/.test(reason || '') && !/ended/.test(reason || '')) text = 'The account is out of credits.';
      return this._finish(text, code === 4408 ? 'ended' : 'error');
    }
    if (code === 1013) return this._finish(MSG[1013], 'error');
    if (!this.everReady && this.attempt >= 2) return this._fail(MSG.unreachable);
    if (this.attempt >= BACKOFF.length) return this._fail(MSG.gaveUp);
    const wait = BACKOFF[this.attempt] * (0.8 + Math.random() * 0.4);
    this.attempt++; this.stats.reconnects++;
    this.setState('reconnecting', `Connection lost - reconnecting (try ${this.attempt}/${BACKOFF.length})...`);
    this.emit('reconnecting', { attempt: this.attempt });
    clearTimeout(this._rcT);
    this._rcT = setTimeout(() => { if (this.running && !this.stopping) this._connect(); }, wait * 1000);
  }
  reconnectNow() {  // the network came back / the tab woke up
    if (!this.running || this.stopping) return;
    if (this.ws && this.ws.readyState === 1 && performance.now() - this.lastRx < 12000) return;
    if (this.ws && this.ws.readyState === 0) return;
    try { if (this.ws) { const w = this.ws; this.ws = null; w.onclose = null; w.close(); } } catch (e) { /* ignore */ }
    clearTimeout(this._rcT);
    if (this.attempt === 0) this.attempt = 1;
    this.setState('reconnecting', 'Reconnecting...');
    this._connect();
  }
  _timers() {
    this._hb = setInterval(() => {
      if (!this.ws || this.ws.readyState !== 1) return;
      this._send({ type: 'ping', t: Date.now() });
      if (performance.now() - this.lastRx > 15000) {  // heartbeat answered by nothing: half-open socket
        const w = this.ws; w.onclose = null; try { w.close(); } catch (e) { /* ignore */ }
        this._closed(4410, 'heartbeat timeout');
      }
    }, 5000);
    this._statT = setInterval(() => {
      if (!this.ready) return;
      this._send({ type: 'client_stats', lag_ms: Math.round(this.stats.lagMax), decode_ms: this.stats.decodeMs,
                   rx_kbps: Math.round(((this.stats.rxVideoBytes - (this._lastVB || 0)) * 8) / 2000) });
      this._lastVB = this.stats.rxVideoBytes; this.stats.lagMax = 0;
    }, 2000);
    this._tickT = setInterval(() => {
      if (this.state === 'speaking' && this.agentDone && this.outCtx && this.outCtx.currentTime >= this.nextT - 0.02) this._listening();
    }, 150);
    this._vis = () => {
      if (document.visibilityState === 'visible') { this.resumeAudio(); this.reconnectNow(); this.reportPhase(); }
    };
    document.addEventListener('visibilitychange', this._vis);
    window.addEventListener('online', this._vis);
    this._hide = null;
    window.addEventListener('pagehide', this._hide = () => {
      try { if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify({ type: 'end' })); } catch (e) { /* ignore */ }
      try { this.ws && this.ws.close(1000, 'page closed'); } catch (e) { /* ignore */ }
    });
  }
  _listening() { this.setState('listening', this.ptt ? 'listening - hold to talk' : 'listening - speak'); }

  /* ---- incoming messages -------------------------------------------------------------------------------------- */
  async _handle(ev, tArrive) {
    const S = this.stats; S.rxMsgs++;
    if (typeof ev.data !== 'string') {
      const n = ev.data.byteLength; S.rxBytes += n;
      if (!this.tagged) { S.rxAudioBytes += n; return this._play(ev.data); }
      const u = new Uint8Array(ev.data);
      if (u[0] === 1) { S.rxAudioBytes += n; return this._play(ev.data.slice(1)); }
      if (u[0] === 2) { S.rxVideoBytes += n; return this._videoBin(u, tArrive); }
      return undefined;
    }
    S.rxBytes += ev.data.length;
    const m = JSON.parse(ev.data);
    switch (m.type) {
      case 'hello_ack': this.tagged = m.framing === 'tagged'; S.tier = m.tier; break;
      case 'pong': S.rtt = Date.now() - (m.t || Date.now()); this.emit('rtt', { ms: S.rtt }); break;
      case 'quality': S.tier = m.tier; this.emit('quality', m); break;
      case 'ready': this._ready(m); break;
      case 'idle_loop': S.rxVideoBytes += ev.data.length; return this._setIdle(await this._decode(m.frames, tArrive), m.fps);
      case 'video_segment': S.rxVideoBytes += ev.data.length;
        this.pendingSeg = { imgs: await this._decode(m.frames, tArrive), fps: m.fps, endPhase: m.end_phase }; break;
      case 'speech_start': this.reportPhase(); this.setState('hearing', 'hearing you...'); break;
      case 'transcript':
        if (m.role === 'user') {
          this.reportPhase(); this.LIVE.tUser = performance.now(); this.LIVE.tFirst = 0;
          this._addLine('user', m.text); this.setState('thinking', 'thinking...');
        } else { this.agentDone = false; this._addLine('agent', m.text); this.setState('speaking', 'speaking'); }
        break;
      case 'agent_start': this.agentDone = false; break;
      case 'agent_done': this.agentDone = true; this.emit('agent_line_end', {}); break;
      case 'interrupted': this.stopPlayback(); this.agentDone = true; this.emit('agent_line_end', {}); this.emit('line', { role: 'sys', text: '[interrupted]', t: this._t() }); this._listening(); break;
      case 'echo': this.emit('echo', { suspected: !!m.suspected, reason: 'server', score: m.score }); break;
      case 'perception_status': case 'scene': this.emit(m.type, m); break;
      case 'error': this.emit('line', { role: 'sys', text: 'error: ' + m.message, t: this._t() }); break;
      default: break;
    }
    return undefined;
  }
  _t() { return Math.max(0, Math.round((Date.now() - this.t0) / 1000)); }
  _addLine(role, text) {
    const l = { role, text, t: this._t() };
    this.log.push(l);
    this.emit('line', l);
  }
  _ready(m) {
    this.ready = true; this.everReady = true; this.attempt = 0;
    if (m.face_url && !this.idleImgs.length) this.emit('face', { url: location.origin + m.face_url });
    this.emit('ready', m);
    if (m.resumed) this.emit('toast', { text: 'Reconnected - the conversation continues.' });
    if (this.ptt) this._send({ type: 'ptt', state: 'on' });
    this._listening();
  }

  /* ---- live face ----------------------------------------------------------------------------------------------- */
  async _decode(frames, tArrive) {
    const t = performance.now();
    const imgs = await Promise.all(frames.map((x) => this._decodeOne(x)));
    this.LIVE.decodeMs = this.stats.decodeMs = Math.round(performance.now() - t);
    this.stats.lagMax = Math.max(this.stats.lagMax, performance.now() - (tArrive || t));
    return imgs.filter(Boolean);
  }
  _decodeOne(x) {
    if (x instanceof Blob && window.createImageBitmap) return createImageBitmap(x).catch(() => null);
    return new Promise((r) => {
      const i = new Image();
      const done = () => { if (i.src.startsWith('blob:')) URL.revokeObjectURL(i.src); };
      i.onload = () => { done(); r(i); }; i.onerror = () => { done(); r(null); };
      i.src = x instanceof Blob ? URL.createObjectURL(x) : 'data:image/jpeg;base64,' + x;
    });
  }
  async _videoBin(u, tArrive) {
    const dv = new DataView(u.buffer, u.byteOffset, u.byteLength);
    const hl = dv.getUint32(1);
    const head = JSON.parse(new TextDecoder().decode(u.subarray(5, 5 + hl)));
    let off = 5 + hl; const blobs = [];
    for (const s of head.sizes) { blobs.push(new Blob([u.subarray(off, off + s)], { type: 'image/jpeg' })); off += s; }
    const imgs = await this._decode(blobs, tArrive);
    if (head.t === 'idle_loop') return this._setIdle(imgs, head.fps);
    this.pendingSeg = { imgs, fps: head.fps, endPhase: head.end_phase };
    return undefined;
  }
  _setIdle(a, fps) {
    this.idleImgs.forEach((i) => i.close && i.close());
    this.idleImgs = a; this.idleFps = fps;
    if (a.length) this.emit('video', { w: a[0].width || a[0].naturalWidth, h: a[0].height || a[0].naturalHeight });
  }
  attachCanvas(c) {
    this.canvas = c; this.vctx = c.getContext('2d');
    const loop = () => { this._draw(); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }
  idleK() { const n = this.idleImgs.length; return n > 1 ? (this.anchor.k + Math.floor((performance.now() - this.anchor.t) / 1000 * this.idleFps)) % (2 * n - 2) : 0; }
  reportPhase() { if (this.idleImgs.length) this._send({ type: 'idle_phase', phase: this.idleK(), fps: this.idleFps }); }
  _draw() {
    const c = this.canvas; if (!c || !this.idleImgs.length) return;
    if (c.width !== (this.idleImgs[0].width || this.idleImgs[0].naturalWidth)) { c.width = this.idleImgs[0].width || this.idleImgs[0].naturalWidth; c.height = this.idleImgs[0].height || this.idleImgs[0].naturalHeight; }
    const L = this.LIVE; let img = null;
    const ct = this.outCtx ? this.outCtx.currentTime : -1;
    this.segs = this.segs.filter((sg) => { const keep = ct < sg.t0 + sg.imgs.length / sg.fps + 0.5; if (!keep) sg.imgs.forEach((i) => i.close && i.close()); return keep; });
    const sg = this.segs.find((x) => ct >= x.t0 && ct < x.t0 + x.imgs.length / x.fps);
    if (sg) {
      this.wasSpeaking = true; if (sg.endPhase != null) this.lastEnd = sg.endPhase;
      img = sg.imgs[Math.min(sg.imgs.length - 1, Math.floor((ct - sg.t0) * sg.fps))];
      if (!L.tFirst && L.tUser) L.tFirst = performance.now(); L.speakingFrames++;
    } else {
      if (this.wasSpeaking) { this.wasSpeaking = false; if (this.lastEnd != null) { this.anchor = { t: performance.now(), k: this.lastEnd }; this.lastEnd = null; } }
      const n = this.idleImgs.length, k = this.idleK(); img = this.idleImgs[k < n ? k : 2 * n - 2 - k]; L.idleFrames++;
    }
    if (img) this.vctx.drawImage(img, 0, 0, c.width, c.height);
  }

  /* ---- agent audio --------------------------------------------------------------------------------------------- */
  stopPlayback() {
    this.sources.forEach((s) => { try { s.onended = null; s.stop(); } catch (e) { /* already stopped */ } });
    this.sources = []; this.nextT = 0;
    this.segs.forEach((sg) => sg.imgs.forEach((i) => i.close && i.close())); this.segs = []; this.pendingSeg = null;
  }
  interrupt() { this.stopPlayback(); this._send({ type: 'interrupt' }); }
  _play(buf) {
    if (!this.outCtx) return;
    const i16 = new Int16Array(buf.byteLength >> 1);
    new Uint8Array(i16.buffer).set(new Uint8Array(buf, 0, i16.length * 2));
    const f = new Float32Array(i16.length); for (let i = 0; i < f.length; i++) f[i] = i16[i] / 32768;
    const ab = this.outCtx.createBuffer(1, f.length, 24000); ab.copyToChannel(f, 0);
    const s = this.outCtx.createBufferSource(); s.buffer = ab; s.connect(this.outGain);
    this.nextT = Math.max(this.nextT, this.outCtx.currentTime + 0.03); s.start(this.nextT);
    if (this.pendingSeg) { this.segs.push({ t0: this.nextT, imgs: this.pendingSeg.imgs, fps: this.pendingSeg.fps, endPhase: this.pendingSeg.endPhase }); this.pendingSeg = null; this.LIVE.segments++; }
    this.nextT += ab.duration;
    this.sources.push(s); s.onended = () => { this.sources = this.sources.filter((x) => x !== s); };
    if (this.state !== 'speaking' && this.state !== 'reconnecting') this.setState('speaking', 'speaking');
  }
  agentLevel() {
    if (!this.analyser || !this.outCtx || this.outCtx.currentTime > this.nextT) return 0;
    const d = new Uint8Array(this.analyser.fftSize); this.analyser.getByteTimeDomainData(d);
    let s = 0; for (let i = 0; i < d.length; i++) { const v = (d[i] - 128) / 128; s += v * v; }
    return Math.sqrt(s / d.length);
  }

  /* ---- "let the agent see me": camera / screen frames for the perception feature ------------------------------- */
  async startShare(kind) {
    if (this.shares[kind]) return this.shares[kind];
    let stream;
    try {
      if (kind === 'camera') stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }, audio: false });
      else stream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 1 }, audio: false });
    } catch (e) {
      const text = e && (e.name === 'NotAllowedError') ? (kind === 'camera' ? 'Camera access was blocked.' : 'Screen sharing was cancelled.')
        : (kind === 'camera' ? 'No camera available.' : 'Screen sharing is not available in this browser.');
      this.emit('toast', { text, bad: true }); return null;
    }
    const v = document.createElement('video'); v.muted = true; v.playsInline = true; v.srcObject = stream; await v.play().catch(() => {});
    const cv = document.createElement('canvas');
    const sh = { kind, stream, video: v, sent: 0, last: 0 };
    sh.timer = setInterval(() => this._shareFrame(sh, cv), 2000);
    stream.getVideoTracks()[0].onended = () => this.stopShare(kind);
    this.shares[kind] = sh;
    this._send({ type: 'perception', enabled: true });
    this.emit('share', { kind, on: true, stream, sh });
    setTimeout(() => this._shareFrame(sh, cv), 400);
    return sh;
  }
  stopShare(kind, silent) {
    const sh = this.shares[kind]; if (!sh) return;
    clearInterval(sh.timer); sh.stream.getTracks().forEach((t) => t.stop()); delete this.shares[kind];
    if (!Object.keys(this.shares).length) this._send({ type: 'perception', enabled: false });
    if (!silent) this.emit('share', { kind, on: false });
  }
  _shareFrame(sh, cv) {
    if (!this.ws || this.ws.readyState !== 1 || !sh.video.videoWidth) return;
    const w = sh.video.videoWidth, h = sh.video.videoHeight, k = Math.min(1, (sh.kind === 'screen' ? 1280 : 640) / w);
    cv.width = Math.round(w * k); cv.height = Math.round(h * k);
    cv.getContext('2d').drawImage(sh.video, 0, 0, cv.width, cv.height);
    cv.toBlob((b) => {
      if (!b || !this.ws || this.ws.readyState !== 1) return;
      const r = new FileReader();
      r.onload = () => { this._send({ type: 'frame', source: sh.kind, jpeg_b64: String(r.result).split(',')[1] }); sh.sent++; this.emit('share_frame', { kind: sh.kind, n: sh.sent, bytes: b.size }); };
      r.readAsDataURL(b);
    }, 'image/jpeg', sh.kind === 'screen' ? 0.7 : 0.6);
  }
}

window.MirageClient = MirageClient;
window.MirageMsg = MSG;
window.MirageVersion = VERSION;
})();
