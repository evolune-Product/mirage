# Realtime client (overnight, agent "rtc")

Goal: make the live conversation robust on real devices with real microphones. Everything below was tested with a
fake mic file in Chromium/Firefox, Playwright mobile emulation and an echo simulation; real microphones, speakers,
phones and flaky networks are NOT verified (see the last section).

## Layout
* `backend/app/static/mirage-client.js` (transport, audio, video, reconnect, PTT, camera/screen), `mirage-ui.js`
  (controls, meters, transcript, banners, shortcuts), `mirage-client.css`. `playground.html` and `guest.html` are now thin
  pages on top (parity by construction). Element ids `go/int/stop/state/log/vid/face` and `window.__live` are unchanged.
* `backend/app/rtc_transport.py` (hello negotiation, binary framing, JPEG tiers, adaptation), `pipeline/echo.py`
  (echo reference), `routers/realtime.py` (pump, resume, heartbeat), `scripts_echo_sim.py`, `tests/test_rtc.py` (9 tests).

## WebSocket protocol additions (all optional; a client that sends no `hello` gets the exact old protocol)
Client -> server JSON: `hello{framing:'tagged', audio:{echoCancellation,noiseSuppression,autoGainControl,ctxRate,resampled}, idle_cached, resume, tier?, max_tier?, mobile, ua}`,
`ping{t}`, `end` (goodbye: marks the conversation as really over), `ptt{state:'on'|'off'|'down'|'up'}`,
`client_stats{lag_ms,decode_ms,rx_kbps}` every 2 s, `frame{source:'camera'|'screen',jpeg_b64}` and `perception{enabled}`
(forwarded to `sess.perception.handle_message`, which the intelligence layer sets; >900 kB messages are dropped).
Server -> client JSON: `hello_ack{framing,tier,heartbeat_s}`, `pong{t}`, `quality{tier,reason}`, `echo{suspected,score}`,
`ready` gains `resumed` and `tier`. After `hello_ack framing=tagged` every binary message has a tag byte: `0x01` agent PCM,
`0x02` video (`u32be header_len`, JSON header `{t:'video_segment'|'idle_loop',fps,sizes[],end_phase?,tier}`, JPEGs back to back).
Query param `resume=1` is accepted on reconnect. Close codes: 4401 key, 4402 ended/credits, 4404 not found, 4408 time limit,
4409 replaced by a newer socket, 4410 heartbeat timeout.

## 1. Echo, headphones, push-to-talk
Server-side `EchoReference` compares the mic loudness envelope with the envelope of the agent audio the server sent
(searches 0-2 s of delay, learns the speaker->mic gain and delay on clean echo, then judges each mic frame by predicted vs
observed level). Mic frames explained by echo are never "speech" (no false barge-in, no false user turn); a user louder than
2x the predicted echo passes. First 0.9 s of a reply accepts no barge-in (echo has not arrived yet; Silero only). Barge-in
now also works while the client is still playing buffered audio after the server finished sending (sends `interrupted`).
The client requests AEC/NS/AGC, reports the granted `getSettings()`, shows "use headphones" when the server flags echo or AEC
is off (hidden when a headset-like output device is detected), and offers push-to-talk (button, Space, `P`).

`scripts_echo_sim.py`: real Session + Silero, 12 Kokoro utterances as agent speech mixed into the mic at -10/-18/-26 dB with
100/250/450 ms delay, reverb and clipping (108 runs). A/B against the previous code (`git archive` of HEAD):

| | before | after |
|---|---|---|
| false barge-ins (echo only) | 72/108 (67%) | 4/108 (4%) |
| false user turns (echo transcribed as the user) | 106/108 | 4/108 |
| real user interrupts over echo at 1.6 s: -26 dB / -18 dB / -10 dB detected | 12/12 (100 ms) / 0/12 / 0/12 | 12/12 (186 ms) / 12/12 (475 ms) / 11/12 (1.4 s) |

Honest limits: the simulation is synthetic (one speaker-mic path model); at -10 dB (basically no AEC) a user as loud as the
echo is hard to separate. The remaining false barge-ins are at -10 dB. Thresholds: `MIRAGE_ECHO_RATIO/CORR/WARMUP_FRAMES`,
`MIRAGE_ECHO_REF=0` disables. Real hardware tuning is needed.

## 2. Connection robustness
States: connecting, listening, hearing, thinking, speaking, reconnecting, error, ended (also `data-state` on `#state`).
Reconnect with backoff (0.4/1/2/4/6/8/8 s, about the server's 30 s resume grace); the mic stays open, `resume=1`, the server
rebuilds LLM history from the stored transcript, does not re-greet, cancels the reaper's end timer, and kicks a half-open
older socket (4409). The client keeps the idle loop (`idle_cached`) so it is not re-sent. Heartbeat every 5 s; the client
drops a socket silent for 15 s, the server closes hello-capable clients silent for 20 s (4410). `end` is sent on Stop and on
`pagehide`; guest sessions that merely drop are not finalised immediately (the reaper does it after the grace period).
Human messages for mic denied / none / busy / insecure context, bad key, ended, out of credits, time limit, taken over, busy.
AudioContext suspended shows "Tap to enable audio"; tab visibility/online events resume audio and reconnect; mic track ended or
device change re-acquires the mic. Verified: Chromium reconnect after a dropped socket (states listening -> reconnecting ->
listening, toast, no errors) and tests for resume/no re-greet, hello/ping, legacy path. Not verified: real network loss,
sleeping phones, bfcache.

## 3. Mobile / cross-browser
Input context asks for 16 kHz; if the browser refuses or ignores it the worklet resamples (area averaging) and there is a
ScriptProcessor fallback when AudioWorklet is missing; output uses a 24 kHz context or the browser resamples buffers.
Verified: Chromium desktop and Pixel 7 emulation (full voice turn + face, no console errors, layout screenshots viewed),
Firefox (16 kHz context + worklet + idle loop decode + mic frames flowing; Firefox's fake mic is a beep so no conversation),
WebKit and iPhone 13 emulation (page loads, correct "microphone blocked" message and layout; Playwright WebKit has no fake mic,
so audio capture/autoplay/AudioContext quirks of real iOS Safari are NOT verified).

## 4. Video transport
Measured on this stack (576x324 frames, 25 fps, per second of agent speech; idle loop of 75 frames is extra, sent once):

| mode | video per second of speech | idle loop |
|---|---|---|
| legacy (base64 in JSON) | 1.09 MB/s (8.7 Mbit/s) | 3.3 MB |
| binary, tier 0 (source JPEGs) | 0.82 MB/s | 2.4 MB |
| binary, tier 1 (default, q55 4:2:0) | about 0.45 MB/s | 1.3 MB |
| tier 2 (0.75 scale, q50) | about 0.27 MB/s | (idle capped at tier 1) |
| tier 3 (0.6 scale, q42) | about 0.17 MB/s | |

Tier frame sizes: 32 / 17.5 / 10.3 / 6.5 KB, PSNR vs source 33.8 / 30.4 / 28.9 dB (tier 1 is visually indistinguishable; viewed). The server
steps down on client-reported lag (>350 ms) or measured send throughput, and back up slowly. Recoding costs about 1.5 ms per frame.
Mobile clients start at tier 2. A reconnecting client skips the idle loop. Skipping redundant frames was not done (the idle loop
is already sent once; segments have no duplicate frames).

## 5. "Let the agent see me"
Checkbox (off by default) -> Camera / Share screen buttons, 2 s JPEG frames (<=640 px camera, <=1280 px screen), visible red
"live" indicator thumbnails, per-source off switch, auto-off when the browser's own stop-sharing is used or the call ends,
`perception enabled` sent on/off. Verified in Chromium with fake camera/screen: 3 camera frames and 2 screen frames sent, indicator visible,
off switch stops everything. Server side is the intelligence layer's `PerceptionManager`; end-to-end "agent answers about what it sees"
was not tested by me.

## 6. Polish
Mic and agent level meters, mute, transcript timestamps, Copy transcript, Copy guest link (playground creates a 7-day guest
link via the API, guest page copies its URL), shortcuts: Space (hold, PTT), M, I, P, V, Esc.

## NOT verified / owner must test on real hardware
Real microphones and speakers (echo thresholds, headphones detection, AEC behaviour per OS/browser), real iOS Safari (sample-rate
quirks, autoplay, background suspension), real Android Chrome, Bluetooth headsets (latency shifts the echo delay), real network
loss and bad-bandwidth adaptation (only logic/unit-tested), screen share on real displays, dashboard iframe permissions on https.
