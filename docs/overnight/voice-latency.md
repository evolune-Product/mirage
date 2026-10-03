# Voice latency (overnight, agent "voice")

Goal: shrink time from the user finishing speaking to (a) first agent audio and (b) first lip-synced frame.

## Headline (real Chromium, fake mic looping 12 questions, live lip-sync face, M1 Pro under heavy shared load)

Measured in the browser from the last loud mic frame sent (`~/scratchpad/pw/voice_e2e.mjs`, medians; p90 in brackets). A/B runs were interleaved (baseline = pre-change code snapshot on its own port, same lip-sync service).

| | before (run 1 / run 2, n=12 / 11) | after (run 1 / run 2, n=10 / 14) |
|---|---|---|
| user transcript shown | 1025 / 965 ms | 603 / 633 ms |
| first agent audio | 3859 / 2832 ms (p90 5.0 / 3.7 s) | 1522 / 2434 ms (p90 2.5 / 4.2 s) |
| first lip-synced frame | 3903 / 3229 ms (p90 5.1 / 7.3 s) | 1586 / 2439 ms (p90 2.6 / 4.2 s) |

Honest caveat on 'after run 2': the first 7 turns of that run were about 1.5-1.7 s like run 1, then the machine got busy (the per-turn server log shows Ollama first token rising from 0.15 s to 0.4-0.9 s and TTS first audio to 1-4 s while other agents ran), which dragged the median to 2.4 s. Inside the server (MIRAGE_LOG_VOICE) a quiet-machine turn shows TTS first audio about 0.7-0.85 s after the transcript even though the standalone benchmark shows about 0.3 s; I did not find the cause (suspects: CPU contention from headless Chromium, sidecar and lip-sync GPU sharing). The browser numbers therefore overstate the standalone ones by roughly 0.4-0.5 s.

An unloaded standalone dev run gave first audio about 1.6 s before vs about 1.0 s after (no browser, `scripts_latency.py`): see the table below. Numbers are noisy because 4 other agents, Ollama and Chrome (swiftshader) share the CPU; the ratio is what to trust.

In-process pipeline benchmark (`backend/scripts_latency.py`, synthetic user fed at real-time pace, n=10-12, median / p90 ms, measured from the end of the user's last speech sample):

| stage | before | after |
|---|---|---|
| endpoint (+STT, now speculative) | 560 + 280 STT | about 470 total |
| LLM first token | 135 | 135 (floor; Ollama options changed nothing) |
| first chunk to TTS | 95 | 85 |
| TTS first audio | 1132 (p90 1584) | 298 (p90 339) |
| first audio, audio only | 2195 (p90 2742) | 1013 (p90 1052) |
| first lip-synced frame | 2401 (p90 3184) | 1197 (p90 1260) |

## What changed

1. **TTS (biggest win)**
   * kokoro-onnx rebuilt its espeak phonemizer on every call (about 250 ms). A persistent `EspeakBackend` makes it under 1 ms (`local.py`).
   * Optional MLX Kokoro sidecar (`mlx_tts_worker.py`, client `mlx_tts.py`): same Kokoro-82M voice on the Apple GPU, first audio of a short phrase 0.13-0.25 s vs 0.45-0.85 s on ONNX/CPU. Runs in its own Python 3.11 venv (`backend/scripts_setup_tts_mlx.sh` creates `<repo>/.venv-tts`, ~1.4 GB) because mlx-audio's dependencies do not install on the backend's Python 3.14. `FallbackTTS` falls back to the ONNX engine if the sidecar is missing or dies; `MIRAGE_TTS=onnx|mlx|auto`.
   * Tried and rejected: int8 Kokoro (2-2.5x slower on M1), CoreML execution provider (no gain), ORT thread counts (noise).
   * Quality: I cannot listen. Objective check: Whisper round-trips MLX and ONNX output to identical text; same weights (bf16 vs fp32), misaki G2P instead of espeak; the MLX output is about 3 dB quieter so the worker applies 1.35x gain. A human should A/B the voices once.
2. **Chunking**: first chunk is 3-7 words (clause break or word cap), later chunks are whole sentences, abbreviations are respected, markdown stripped; tiny pauses (0.10-0.16 s) are appended after chunks (`local.sentences`).
3. **Endpointing**: speculative STT starts after 150 ms of silence; when the transcript is a complete sentence (ends in . ? !, not an ellipsis, not a dangling word) the turn ends at 350 ms of silence, otherwise at the hard 700 ms timeout. A resumed utterance drops the speculation. `MIRAGE_PAUSE_MS`, `MIRAGE_MIN_COMMIT_MS`, `MIRAGE_EARLY_COMMIT=0` to disable.
4. **STT**: greedy decoding, no timestamps, no second VAD pass (same WER on my set, about 200 ms vs 280 ms). Kept base.en: tiny.en is 105 ms but its speculative run is already hidden inside the endpoint window, so the extra errors on real accents are not worth it. small.en 650 ms and distil-small.en 550 ms are too slow on CPU.
5. **VAD**: Silero VAD v5/6 through onnxruntime (`pipeline/assets/silero_vad.onnx`, 0.13 ms per 32 ms chunk), automatic fallback to the energy gate; tests with fake providers keep the energy gate. See `scripts_vad_eval.py`:
   * false barge-ins on 12 s of loud hiss, 50 Hz hum, music chord: energy gate 3/3, Silero 0/3 (keyboard clicks and quiet fan: neither).
   * speech onset: energy median -7 ms, Silero +50 ms; barge-in fires 93 ms (energy) vs 157 ms (Silero + loudness gate) after onset.
   * echo of the agent 18 dB down: energy 9/12 false barge-ins, Silero alone 12/12 (level-invariant), Silero + the loudness gate used while the agent talks (stricter probability 0.7 and rms >= 600) 2/12. This is a mitigation, not echo cancellation; browser AEC remains the real fix.
6. **Pipeline parallelism**: LLM, chunker and TTS run in one stage, lip-sync render and send in another, joined by a queue. With a face, pieces are progressive (0.35 s, 0.6 s, then 1.0 s: render time is about 40 ms + 0.3x length) and never leave a tail under 0.25 s; audio under 0.3 s is zero-padded for rendering and the frames trimmed back (the lip-sync server errors below about 0.2 s of audio). One failed render no longer switches the face off for the call (3 consecutive failures after it has worked; immediately if it never worked).
7. **Warm-up**: Whisper kernel compile at model load, Ollama model load plus system-prompt prefill (`OllamaLLM.warmup`), MLX sidecar start, all awaited before `ready` and concurrent with the lip-sync setup. Cold first connect pays about 9 s once per server process (a startup preload would hide it; `main.py` is not mine).
8. **Lip-sync continuity (requested by the face agent)**: the playground reports its idle ping-pong index (`{"type":"idle_phase","phase":k,"fps":25}` on speech start and on the user transcript); the first piece of a reply is rendered with `?phase=` (advanced by elapsed time + 0.15 s) and `fade_in`; `video_segment` carries `end_phase`, and the playground resumes its idle loop from it. `LipsyncClient.prepare()` is called as soon as the conversation connects.
9. **Idle loop no longer blocks the mic** (coordinator request): `idle_loop` is fetched by a background task (`send_idle_loop`), `ready` and the receive loop start immediately, and a failure just disables the face. Test: `test_idle_loop_fetch_is_background_and_failure_disables_face`.
10. Diagnostics: `MIRAGE_LOG_VOICE=1` logs per-turn `stt_s, llm_ft_s, chunk1_s, tts_ft_s, ttfa_s`.

WebSocket protocol is backward compatible: only additive fields (`end_phase` on `video_segment`) and one additive client message (`idle_phase`).

## Verified
* 103+ backend tests pass (new tests for Silero vs noise, pause/force_end, utterance_complete, chunker, early commit, speculation dropped on resume, tiny-piece padding and phase, TTS fallback, background idle loop).
* Browser E2E with a real Chromium, fake mic, 10+ turns, live face, no console errors; transcripts correct on all questions.
* Whisper round trip on both TTS engines.

## Could NOT verify
* Subjective voice quality of MLX vs ONNX Kokoro, naturalness of the sub-clause first chunks and the added pauses.
* Real microphones, real speakers and echo (fake mic file only); the echo guard is tuned on synthetic attenuation.
* Early-commit false cut-offs on real, hesitant speakers (synthetic fluent speech only). Set `MIRAGE_EARLY_COMMIT=0` or raise `MIRAGE_MIN_COMMIT_MS` if users get cut off.
* Absolute numbers on an idle machine; the shared box was at load 5-7 during E2E.

## Not done / next steps
* The remaining floor is endpointing (about 350 ms + VAD lag) + LLM first token 135 ms + TTS 130-300 ms + first render 130-200 ms. Ideas: a small semantic end-of-turn model, streaming STT (sherpa-onnx) so no speculation is needed, a faster or quantised LLM tier, streaming lip-sync frames instead of 0.35 s pieces, and sending audio before video for the first piece.
* Preload models at server start (needs a lifespan hook in `main.py`).
* Reference-based echo cancellation (correlate mic with sent agent audio).
* `_say()` (greeting) still has its own render loop; it should call `_emit_audio`.

## Reproduce
```
cd backend && .venv/bin/python scripts_latency.py --n 10 [--lipsync r_xxx]     # stage table; MIRAGE_APP_ROOT=<old tree> for A/B
.venv/bin/python scripts_vad_eval.py
node voice_e2e.mjs   # Playwright harness (scratchpad), API=http://localhost:8010 TURNS=10
```
