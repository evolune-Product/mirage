# Capacity + reliability (agent "cap") - working notes, Oct 3 2026

Machine: M1 Pro 34 GB, Apple MPS, **shared with 4 other agents running GPU/CPU jobs the whole time** (GPU 60-100 % busy before
my load started, load average 8-11). Every number below is "under contention"; absolute values are pessimistic, A/B pairs are the
meaningful part. Own instances only (api 8440, lipsync 8441).

## Tools (all new)
* `backend/loadtest/loadgen.py`: N simultaneous real WebSocket conversations (hello/tagged binary like the browser, Kokoro-synthesised
  questions replayed at real-time 20 ms pacing, mic kept open with silence), voice-only and live-face modes; per turn end-of-speech ->
  first audio / first video segment, dropped turns (45 s timeout), error events, close codes; 1 Hz CPU/RSS of api/lipsync/ollama,
  Apple GPU % (ioreg), background load before each run, server stage timings parsed from `VOCALFACE_LOG_VOICE=1` logs. Prints a table.
  `cd backend && .venv/bin/python -m loadtest.loadgen --levels 1,2,3,5,8 --mode both --turns 3 --api-log /tmp/cap_api.log`
* `backend/loadtest/leakcheck.py`: 100 connect/disconnect cycles (polite / abrupt / mid-reply) comparing tasks/threads/fds/RSS.

## Baseline (code at b7693af, 3 turns per conversation, first-audio = end of user speech -> first agent audio byte)
| mode | N | convos fully ok | turns ok | first-audio med / p90 (s) | server TTS first chunk (s) | GPU mean % |
|---|---|---|---|---|---|---|
| voice | 1 | 1/1 | 3/3 | 1.50 / 1.58 | 0.79 | 38 |
| voice | 2 | 2/2 | 6/6 | 1.84 / 1.87 | 1.21 | 47 |
| voice | 3 | 3/3 | 9/9 | 1.61 / 2.59 | 1.05 | 68 |
| voice | 5 | 4/5 | 14/15 | 9.59 / 21.3 | 8.91 | 56 |
| voice | 8 | 0/8 | 7/24 | 4.99 / 8.46 | 19.3 | 83 |
| face | 1 | 1/1 | 3/3 | 2.13 / 2.14 (warm 1.44) | 0.91 | 66 |
| face | 2 | 2/2 | 6/6 | 1.98 / 2.43 | 1.15 | 67 |
| face | 3 | 3/3 | 9/9 | 2.61 / 4.04 | 1.63 | 80 |
| face | 5 | 0/5 | 10/15 | 3.58 / 7.84 (5 timeouts) | 6.28 | 90 |
| face | 8 | 0/8 | 0/24 | none | - | 87 |
(run 1; my own pytest run overlapped the N=5 rows, so those are worse than a clean run.) Cliff between 3 and 5 in both modes.

## Bottlenecks found (code reading + measurements)
1. **TTS sidecar**: one MLX Kokoro process serves all sessions, and its protocol let a *newer request abandon the older one*
   (the worker dropped the rest of request A when B arrived): under concurrency this truncates other users' audio. Fixed: explicit
   `{"cancel": id}`, pool of `VOCALFACE_TTS_WORKERS` sidecars (default 2), least-busy routing.
2. **lipsync `/render` ran inference + composite + JPEG on the event loop** behind a plain lock: the whole service froze per render,
   lock hand-over was arbitrary, abandoned requests still rendered, no queue bound, per-replica cursor shared by all sessions of a
   replica (guest links = many sessions, one replica). Fixed: `workers/lipsync_sched.py` (2 lane threads, first-piece priority + aging,
   per-session GPU-time fairness, drop on client disconnect / stale deadline, 503 when the queue is full, per-session cursors,
   per-replica build lock).
3. Backend lip-sync client opened a new HTTP connection per 0.35 s piece (now pooled), 15 s render timeout that stalled audio
   (now switch face off at once on timeout; periodic health re-probe brings it back).
4. Failure modes: LLM error left the client silent (now spoken apology + `agent_done`); hung Ollama hung the turn (first-token 25 s /
   stall 15 s timeouts); utterance buffer unbounded (60 s cap); SIGKILL left conversations `active` forever and unbilled
   (`resilience.recover_orphans`), SIGTERM now closes sockets with 1012 and waits for teardown.

## Admission control
`backend/app/admission.py`, wired in `routers/realtime.py` (guest links go through the same function): VOCALFACE_MAX_CONVOS, VOCALFACE_MAX_FACE_CONVOS,
face overflow -> voice-only, optional queue, `busy` JSON + close 1013, `/health/capacity` (503 when full), `/health/deep` and `/metrics` gauges
(live per kind, limits, admission counters, loop lag, fds, tasks, RSS). Tests: `tests/test_capacity*.py` (37).

## After-change measurements
(see below, appended as runs complete)
- voice N=1: first-audio 2.62 s, N=5: 4.58 / 6.02 s, 5/5 conversations and 15/15 turns ok (baseline N=5: 9.6 / 21.3 s, 4/5). Background GPU was 80 % for the N=1 run, so N=1 is inflated.

## After-change measurements (same loadgen, new code; heavy background GPU 90-100 % from other agents)
| mode | N | convos ok | turns ok | first-audio med / p90 (s) | warm med (s) |
|---|---|---|---|---|---|
| voice | 5 | 5/5 | 15/15 | 4.58 / 6.02 | 4.58 |
| face | 1 | 1/1 | 3/3 | 3.76 / 7.93 | 3.17 |
| face | 3 | 3/3 | 9/9 | 4.65 / 10.5 | 3.74 |
| face | 5 | 5/5 | 15/15 | 7.73 / 14.7 | 6.48 |
| face | 8 | 5/8 | 21/24 | 6.80 / 10.3 | 5.96 |
Before: face N=5 0/5 (10/15 turns), face N=8 0/24, voice N=5 4/5. The cliff is gone (no drops until N=8) but latency still grows with N:
the GPU was 92-99 % busy before my load started, so absolute latencies are inflated; a quiet-machine series was not possible.
Remaining saturation order: GPU (TTS + Wav2Lip + others) first, Ollama queueing second (first token 0.3 s -> 5 s at N=5; not tuned:
OLLAMA_NUM_PARALLEL / num_ctx 4096 would help but needs a private Ollama; ~4 GB KV for a 1B model at the default 32k ctx), API event loop
last (20-55 % of one core, loop lag max 11 ms). Not done: soak test, disk-full simulation, Ollama-parallelism A/B.

## Leak check (100/60/30 connect-disconnect cycles, polite/abrupt/mid-reply)
Found and fixed a real leak: `ConversationRuntime._watchdog` (sleeps for the whole call limit) survived every call (31 tasks after 30 cycles)
because teardown could be cancelled before `rt.stop()` ran. `realtime.py` now cancels runtime/session tasks before its first await.
After: tasks 8 -> 8, slots 0, fds 39 -> 42 (stable), threads 7 -> 11 (pool growth, not per call), RSS falls back. First 100-cycle run had 41
failures only because signup is rate limited (VOCALFACE_RATE_LIMIT=0 for load tests). `VOCALFACE_DEBUG_TASKS=1` adds `tasks_by_coro` to /health/capacity.

## Honest limits
Defaults 6 total / 3 face are conservative and derived under contention. Chaos tests are fakes (no real SIGKILL of lipsync/Ollama run);
SIGTERM path unit-tested but not exercised against a live uvicorn. Multi-process deployments need sticky routing; limits are per process.
