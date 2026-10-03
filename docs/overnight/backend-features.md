# Overnight report: backend-features (Oct 3 2026)

Goal: competitor feature parity for the Mirage backend, exposed through the API (no dashboard UI). Parity table and prioritised plan: `docs/FEATURE_PARITY.md`. API reference: `docs/API.md`, section "Feature layer".

## Result
- Backend tests: **95 passed** (was 43 before this work; 33 are new in `backend/tests/test_features.py`; ran the full suite 3 times, no flakes after fixing one race in my own test).
- Real end-to-end runs against the live local stack (Whisper, Kokoro, Ollama qwen3:8b / llama3.2:1b, Chromium via Playwright with a fake mic file) on an isolated backend (port 8020, DB `/tmp/feat.db`): all of the items below marked "live".
- No git commits or pushes, no external services contacted except downloading the open-source faster-whisper `base` and `small` models and the `cryptography` package.

## What was built, and how it was verified

| # | Feature | Verified |
|---|---|---|
| 1 | Transcripts persisted per turn, `GET /conversations/{id}/transcript` (json/text), end-of-conversation LLM summary saved as persona memory, memories injected into later conversations (scoped by `participant_id`), metrics row | unit + **live** (conv 2 for the same participant recalled "Pro Plan" question, a different participant did not) |
| 2 | Webhooks: endpoints CRUD, signing secret (shown once), HMAC-SHA256 `Mirage-Signature` (Stripe-style, timestamped), DB-backed queue with leases, backoff 10s/1m/5m/30m/2h, delivery log, manual retry, test ping; events conversation.started/ended, transcript.ready, objective.completed, tool.called, guardrail.triggered, replica.ready/error, video.ready/error, video_batch.completed | unit (signature round trip, retries, give-up, filtering, lease against double send, real HTTP receiver) + **live** (fake receiver on :8721 got started, tool.called, objective.completed, ended, transcript.ready) |
| 3 | Persona config: language, greeting (agent speaks first), objectives (LLM-judged, variable extraction), guardrails (prompt rules + clause-level output filter + fallback line), custom OpenAI-compatible LLM (key encrypted, never returned), tools/function calling (signed webhook, result spoken, call log) | unit for all; **live**: greeting spoken first with `{{first_name}}` rendered, qwen3:8b called the `get_plan_price` tool through the real webhook and spoke "The Pro plan costs $79 per month", objective `ask_price` completed with extracted variable. Guardrail filter and custom OpenAI LLM verified only with fakes / a local fake OpenAI server, not a hosted vendor |
| 4 | Multilingual: persona/conversation `language` (en en-gb es fr hi it pt ja zh + auto + STT-only languages), Whisper multilingual model, Kokoro voice/espeak language per language, `GET /v1/voices`, `/v1/languages`; video renderer now picks the Kokoro language from the voice id | unit + **live Spanish and Hindi** (spoken question in Spanish transcribed correctly, agent answered in Spanish with audio; Hindi transcribed in Devanagari with the `small` model, answered in Hindi). `base` Whisper gave Urdu-script garbage for Hindi, hence `small` is the default for hi/ja/zh/etc. Not verified: fr, it, pt, ja, zh, auto |
| 5 | API keys: multiple hashed keys per account, create/list/revoke, legacy `Account.api_key` kept, legacy rotate; works for WebSockets | unit; legacy key verified live against a copy of the existing `mirage.db` |
| 6 | Analytics `GET /v1/analytics`: conversations/minutes per day, first-audio latency avg/p50/p95, top personas, video counts, tool calls, interruptions; per-conversation metrics stored | unit; latency number checked live (4.6 s for a tool-calling qwen3:8b reply, includes STT) |
| 7 | Video: `{{variables}}` + preview, bulk from rows (batch status, per-row validation, moderation), translation variants (LLM per language + language voice), video webhooks, batch-complete event | unit only. No real render and no real LLM translation was run, the worker was not started |
| 8 | Guest share links: create/list/revoke, public `/guest/{token}` page (reuses playground JS, countdown), per-session seconds, total cost cap incl. reserved seconds of running sessions, per-link and per-IP hourly limits, expiry, revoke, owner credit check, usage counted to the owner | unit + **live in Chromium**: guest page greeted "Welcome to the Mirage demo!", usage counted on close, `conversation.ended` fired. The 4408 time-limit close is unit-tested, not watched live in a browser |

Extras added because they were needed to make the above correct: conversation `max_seconds` and mid-call credit enforcement (closes with 4408), idle reaper (ends abandoned conversations after `MIRAGE_END_GRACE_S`, charging credits once; before this, a conversation whose client never called `/end` was never charged), qwen3 `think=false` fix in the feature backend (without it qwen3 spends the whole 90-token voice budget on thinking and the reply is truncated or empty), `MIRAGE_JUDGE_MODEL`, SDK updates.

## Changes to shared files (small, targeted)
- `app/auth.py`: now `account_for_key()` (hashed keys then legacy), `current_account` unchanged in signature.
- `app/main.py`: lifespan starts the webhook delivery loop and the idle reaper (disable with `MIRAGE_WEBHOOK_LOOP=0`). Routers auto-register.
- `app/routers/realtime.py`: key lookup via `account_for_key`; builds a `ConversationRuntime` (wrapped in try/except so a feature bug never blocks a call), wraps providers + `send_json`, `rt.start()` after `ready`, `rt.stop()` in `finally`. WebSocket protocol unchanged; new additive events `objective_completed`, `guardrail`.
- `app/pipeline/session.py`: added `Session.say(text)` (greeting) at the end of the class only.
- `app/routers/resources.py`: `POST /conversations` accepts optional extra fields and records them; `end` schedules `finalize_conversation` as a background task.
- `app/jobs.py`: Kokoro language from voice id; `replica.*` / `video.*` webhook events after a job finishes.
- New: `models_features.py` (all new tables), `secretbox.py`, `webhooks.py`, `events.py`, `languages.py`, `llm_backends.py`, `convo_runtime.py`, routers `webhooks_api, persona_config_api, conversations_api, keys_api, analytics_api, voices_api, video_features_api, guest_api, guest_page`, `static/guest.html`, tests, SDK methods (python + js, `dist` rebuilt), `requirements.txt` (+cryptography).
- Existing DB files keep working: only new tables are created (checked by booting against a copy of `backend/mirage.db`).

## Known gaps and honest caveats
- **Objective judging is as good as the judge model.** Judging with the persona's 1B voice model will miss completions; use `MIRAGE_JUDGE_MODEL=qwen3:8b`. Live, the judge on qwen3:8b took ~14 s after the turn and competes with the voice LLM for the same Ollama (it runs after `agent_done`, so it can slow the next turn on a single small machine).
- **Latency**: the feature layer adds no work before the first audio on the default path (no tools/guardrails). With tools, the reply costs two LLM rounds (4.6 s first audio live with qwen3:8b). Guardrail filtering buffers by clause (comma/sentence) so first-audio is not delayed much, but clause-spanning violations can slip through (it checks the previous clause plus the current one).
- A greeting that the user talks over (barge-in) is cancelled and, like any interrupted sentence, is not written to the transcript (seen once live when the user's question overlapped a slow first greeting).
- Summaries/memories are written when the conversation ends; if a client calls `/end` while an answer is still being spoken, the in-flight reply is missing from that summary. Transcript turns themselves are written live.
- Webhook/tool/custom-LLM URLs may point at private addresses by default (needed for local dev); set `MIRAGE_BLOCK_PRIVATE_URLS=1` in production. The check resolves DNS at registration only, not at send time (a rebinding attack is not covered).
- Webhook delivery is at-least-once with leases for multi-process safety; verified with a re-entrancy test, not with real concurrent processes.
- Translation: LLM script translation + re-render, not time-aligned dubbing, and voices are Kokoro presets (no cloning). Hindi/other-language TTS quality was heard by transcript only, I did not listen to the audio.
- The guest page is a copy of the playground JS, it will not pick up later playground changes automatically. Guests share the owner's credit pool; the only abuse controls are the per-link/per-IP limits and caps described above (IP-based limits are weak behind shared NATs and need `MIRAGE_TRUST_PROXY` behind a proxy).
- Not built: recordings, perception/screen share, Magic-Canvas components, MCP, teams/SSO, image-to-replica, voice cloning (see FEATURE_PARITY.md priorities).
- Whisper `small` (and `base`) are downloaded into `~/.cache/huggingface` on this Mac; a fresh deployment downloads them on first non-English conversation, which adds a one-off delay.

## How to re-run
- Tests: `cd backend && .venv/bin/python -m pytest -q` (set nothing; hardening rate limits are reset inside my fixtures).
- Live harness used tonight (scratchpad, not in the repo): `scratchpad/feat/e2e.mjs [en|es|hi|guest]`, `wsclient.py`, `mem.py`, `fake_hooks.py` (receiver on :8721). Isolated backend: `MIRAGE_DB_URL=sqlite:////tmp/feat.db MIRAGE_DATA=/tmp/feat_data MIRAGE_CORS_ORIGINS='*' .venv/bin/uvicorn app.main:app --port 8020`.
