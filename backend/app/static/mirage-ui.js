/* Controls, transcript, meters and banners for MirageClient (shared by playground.html and guest.html). */
(() => {
'use strict';
const $ = (id) => document.getElementById(id);
const fmt = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
const el = (tag, attrs, html) => { const e = document.createElement(tag); Object.assign(e, attrs || {}); if (html != null) e.innerHTML = html; return e; };

function mount(root, c, cfg = {}) {
  root.innerHTML = `
  <div id="mx-audio" class="mx-banner warn" hidden><span>Your browser is blocking audio until you interact with the page.</span><button id="mx-audio-btn">Tap to enable audio</button></div>
  <div id="mx-echo" class="mx-banner warn" hidden><span id="mx-echo-t">Use headphones for best results: the agent's voice seems to be leaking into your microphone.</span><span><button id="mx-echo-ptt" class="sec">Use push-to-talk</button><button class="x" id="mx-echo-x" aria-label="Dismiss">&times;</button></span></div>
  <div id="mx-err" class="mx-banner bad" hidden></div>
  <div class="row"><button id="go">${cfg.startLabel || 'Start'}</button><button id="int" class="sec" disabled>Interrupt</button><button id="stop" class="sec" disabled>Stop</button>
    <button id="mx-mute" class="sec" disabled aria-pressed="false">Mute</button><button id="mx-ptt" class="sec" disabled aria-pressed="false" title="Hold a button or the space bar to talk (best with speakers)">Push-to-talk</button></div>
  <div class="row"><button id="mx-hold" class="sec" hidden>Hold to talk</button></div>
  <img id="face" alt="agent"><canvas id="vid"></canvas>
  <div id="state" data-state="idle" role="status" aria-live="polite">idle</div>
  <div class="mx-meters"><div class="mx-meter" id="mx-mic"><span>mic</span><i><b></b></i></div><div class="mx-meter agent" id="mx-ag"><span>agent</span><i><b></b></i></div>
    <span class="mx-pill" id="mx-hp" hidden>headphones</span><span class="mx-pill" id="mx-rtt" hidden></span><span class="mx-pill" id="mx-q" hidden></span></div>
  <label class="mx-check"><input type="checkbox" id="mx-see-on"> Let the agent see me <span class="s">(camera / screen, off by default)</span></label>
  <div class="mx-see" id="mx-see" hidden><p>While this is on, a small picture is sent to the agent every 2 seconds so it can answer questions about what it sees. Nothing is sent when it is off. A red recording indicator shows whenever you are sharing.</p>
    <div class="row"><button id="mx-cam" class="sec">Camera</button><button id="mx-scr" class="sec">Share screen</button></div><div class="mx-thumbs" id="mx-thumbs"></div><div class="s" id="mx-scene"></div></div>
  <div id="log" aria-live="off"></div>
  <div class="row"><button id="mx-copy" class="sec">Copy transcript</button>${cfg.linkButton ? `<button id="mx-link" class="sec">${cfg.linkButton}</button>` : ''}</div>
  <div class="mx-keys"><kbd>Space</kbd> hold to talk (push-to-talk on) &middot; <kbd>M</kbd> mute &middot; <kbd>I</kbd> interrupt &middot; <kbd>P</kbd> push-to-talk &middot; <kbd>V</kbd> camera &middot; <kbd>Esc</kbd> stop</div>
  <div id="mx-toast" role="status"></div>`;
  const log = $('log'), go = $('go'), stop = $('stop'), intb = $('int'), mute = $('mx-mute'), pttb = $('mx-ptt'), hold = $('mx-hold');
  c.attachCanvas($('vid'));
  let cur = null, echoDismissed = false;
  const toast = (text, bad) => { const d = el('div', { className: bad ? 'bad' : '', textContent: text }); $('mx-toast').appendChild(d); setTimeout(() => d.remove(), bad ? 6000 : 3500); };
  const running = (on) => { go.disabled = on; stop.disabled = intb.disabled = mute.disabled = pttb.disabled = !on; if (!on) { hold.hidden = true; } };
  const sys = (t) => { const d = el('div', { className: 's', textContent: t }); log.appendChild(d); log.scrollTop = 1e9; };

  c.on('state', ({ state, text }) => { const s = $('state'); s.textContent = text; s.dataset.state = state; });
  c.on('ready', () => { running(true); $('mx-err').hidden = true; });
  c.on('face', ({ url }) => { const f = $('face'); f.src = url; f.style.display = 'block'; });
  c.on('video', () => { $('vid').style.display = 'block'; $('face').style.display = 'none'; });
  c.on('line', (l) => {
    if (l.role === 'sys') { cur = null; return sys(l.text); }
    if (l.role === 'user') { cur = null; const d = el('div', { className: 'u' }, `<span class="ts"></span>`); d.firstChild.textContent = fmt(l.t); d.appendChild(document.createTextNode('You: ' + l.text)); log.appendChild(d); }
    else {
      if (!cur) { cur = el('div', { className: 'a' }, `<span class="ts"></span>`); cur.firstChild.textContent = fmt(l.t); cur.appendChild(document.createTextNode('Agent: ')); log.appendChild(cur); }
      cur.appendChild(document.createTextNode(l.text + ' ')); $('face').classList.add('talk');
    }
    log.scrollTop = 1e9;
  });
  c.on('agent_line_end', () => { cur = null; $('face').classList.remove('talk'); });
  c.on('toast', (t) => toast(t.text, t.bad));
  c.on('fatal', ({ text }) => { running(false); const e = $('mx-err'); e.textContent = text; e.hidden = false; sys(text); });
  c.on('ended', ({ text }) => { running(false); sys(text); $('face').classList.remove('talk'); });
  c.on('audio_blocked', ({ blocked }) => { $('mx-audio').hidden = !(blocked && c.running); });
  $('mx-audio-btn').onclick = () => c.resumeAudio();
  c.on('echo', (e) => {
    if (!e.suspected) { $('mx-echo').hidden = true; return; }
    if (echoDismissed || c.headphones || c.ptt) return;
    $('mx-echo-t').textContent = e.reason === 'aec-off'
      ? "Your browser is not cancelling echo for this microphone. Use headphones for best results, or switch to push-to-talk."
      : "Use headphones for best results: the agent's voice seems to be leaking into your microphone, which can make it interrupt itself.";
    $('mx-echo').hidden = false;
  });
  $('mx-echo-x').onclick = () => { echoDismissed = true; $('mx-echo').hidden = true; };
  $('mx-echo-ptt').onclick = () => { setPtt(true); $('mx-echo').hidden = true; };
  c.on('headphones', ({ on }) => { $('mx-hp').hidden = !on; if (on) $('mx-echo').hidden = true; });
  c.on('rtt', ({ ms }) => { const r = $('mx-rtt'); r.hidden = false; r.textContent = ms + ' ms'; });
  c.on('quality', (q) => toast('Video quality ' + (q.reason === 'lag' ? 'lowered' : 'raised') + ' for your connection'));
  c.on('ready', (m) => { const q = $('mx-q'); if (m.tier != null) { q.hidden = false; q.textContent = 'video tier ' + m.tier; } });
  c.on('quality', (m) => { $('mx-q').hidden = false; $('mx-q').textContent = 'video tier ' + m.tier; });

  go.onclick = async () => { cfg.beforeStart && cfg.beforeStart(); $('mx-err').hidden = true; log.textContent = ''; cur = null; echoDismissed = false; await c.start(); };
  stop.onclick = () => c.stop();
  intb.onclick = () => c.interrupt();
  mute.onclick = () => c.setMuted(!c.muted);
  c.on('muted', ({ muted }) => { mute.classList.toggle('on', muted); mute.textContent = muted ? 'Unmute' : 'Mute'; mute.setAttribute('aria-pressed', muted); });
  function setPtt(on) {
    c.setPtt(on); pttb.classList.toggle('on', on); pttb.setAttribute('aria-pressed', on); hold.hidden = !on || !c.running;
    $('state').textContent = on ? 'listening - hold to talk' : 'listening - speak';
    toast(on ? 'Push-to-talk on: hold the button or space bar while you speak.' : 'Push-to-talk off: just speak.');
  }
  pttb.onclick = () => setPtt(!c.ptt);
  c.on('ready', () => { hold.hidden = !c.ptt; });
  c.on('ptt', ({ down }) => hold.classList.toggle('down', !!down));
  hold.addEventListener('pointerdown', (e) => { e.preventDefault(); try { hold.setPointerCapture(e.pointerId); } catch (x) { /* ignore */ } c.pttPress(); });
  ['pointerup', 'pointercancel', 'lostpointercapture'].forEach((n) => hold.addEventListener(n, () => c.pttRelease()));
  hold.addEventListener('contextmenu', (e) => e.preventDefault());

  // ---- let the agent see me ----
  const see = $('mx-see');
  $('mx-see-on').onchange = (e) => { see.hidden = !e.target.checked; if (!e.target.checked) { c.stopShare('camera'); c.stopShare('screen'); } };
  if (!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia)) $('mx-scr').style.display = 'none';
  const shareBtn = (id, kind) => { $(id).onclick = () => (c.shares[kind] ? c.stopShare(kind) : (c.running ? c.startShare(kind) : toast('Start the conversation first.', true))); };
  shareBtn('mx-cam', 'camera'); shareBtn('mx-scr', 'screen');
  c.on('share', ({ kind, on, stream }) => {
    const b = $(kind === 'camera' ? 'mx-cam' : 'mx-scr'); b.classList.toggle('on', on); b.textContent = on ? (kind === 'camera' ? 'Stop camera' : 'Stop sharing') : (kind === 'camera' ? 'Camera' : 'Share screen');
    const id = 'mx-th-' + kind; const old = $(id); if (old) old.remove();
    if (on) { const t = el('div', { id, className: 'mx-thumb' }, '<span class="mx-rec" title="The agent can see this">' + (kind === 'camera' ? 'camera' : 'screen') + ' live</span>'); const v = el('video'); v.muted = true; v.playsInline = true; v.autoplay = true; v.srcObject = stream; t.appendChild(v); $('mx-thumbs').appendChild(t); }
  });
  c.on('scene', (m) => { $('mx-scene').textContent = m.text ? 'Agent sees: ' + m.text : ''; });
  c.on('perception_status', (m) => { if (m.reason && !m.enabled) $('mx-scene').textContent = 'The agent is not set up to see you (' + m.reason + ').'; });

  // ---- transcript / link ----
  $('mx-copy').onclick = async () => {
    const t = c.log.map((l) => `[${fmt(l.t)}] ${l.role === 'user' ? 'You' : 'Agent'}: ${l.text}`).join('\n') || log.innerText;
    await copyText(t); toast('Transcript copied');
  };
  if (cfg.linkButton) $('mx-link').onclick = async () => { try { const u = await cfg.getLink(); await copyText(u); toast('Link copied: ' + u); } catch (e) { toast(e.message || 'Could not create a link', true); } };

  // ---- keyboard ----
  const typing = (e) => /^(INPUT|TEXTAREA|SELECT)$/.test((e.target || {}).tagName || '') || (e.target && e.target.isContentEditable);
  document.addEventListener('keydown', (e) => {
    if (typing(e) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.code === 'Space' && c.running && c.ptt) { e.preventDefault(); if (!e.repeat) { c.pttPress(); } return; }
    if (!c.running || e.repeat) return;
    const k = e.key.toLowerCase();
    if (k === 'm') c.setMuted(!c.muted); else if (k === 'i') c.interrupt(); else if (k === 'p') setPtt(!c.ptt);
    else if (k === 'v') { const cb = $('mx-see-on'); cb.checked = !cb.checked; cb.onchange({ target: cb }); }
    else if (e.key === 'Escape') c.stop();
  });
  document.addEventListener('keyup', (e) => { if (e.code === 'Space' && c.ptt && !typing(e)) { e.preventDefault(); c.pttRelease(); } });
  window.addEventListener('blur', () => c.pttRelease());

  // ---- meters (one rAF loop) ----
  const mic = $('mx-mic'), ag = $('mx-ag'); let sm = 0, sa = 0;
  (function loop() {
    sm = Math.max(c.micLevel * 3.2, sm * 0.85); sa = Math.max(c.agentLevel() * 3.2, sa * 0.85);
    mic.firstElementChild.nextElementSibling.firstChild.style.width = Math.min(100, sm * 100) + '%';
    ag.firstElementChild.nextElementSibling.firstChild.style.width = Math.min(100, sa * 100) + '%';
    mic.classList.toggle('muted', c.muted);
    requestAnimationFrame(loop);
  })();
  return { toast, sys, running };
}

async function copyText(t) {
  try { await navigator.clipboard.writeText(t); return; } catch (e) { /* fall through (insecure context / iframe policy) */ }
  const a = document.createElement('textarea'); a.value = t; a.style.position = 'fixed'; a.style.opacity = '0'; document.body.appendChild(a); a.select();
  try { document.execCommand('copy'); } finally { a.remove(); }
}

window.MirageUI = { mount };
})();
