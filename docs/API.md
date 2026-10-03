# Mirage API (v1)

Auth: header `x-api-key: mk_...` on everything except `/v1/signup`, `/v1/billing/plans`, webhooks, `/health`. Errors are `{"detail": ...}`. Interactive spec: `/docs`, raw: `/openapi.json`. Route list below was taken from the app's generated OpenAPI. Routes owned by other modules (knowledge, realtime) are listed with their request shapes at time of writing; check `/openapi.json` for changes.

## Account
- `POST /v1/signup` `{email}` -> `{account_id, api_key, credits_seconds}` (600 free seconds)
- `GET /v1/usage` -> `{credits_seconds}`
- `GET /v1/usage/ledger?limit=` -> ledger rows (`kind`: topup|plan|usage|grant|adjust, `seconds` +/-, `amount_cents`, `ref`)
- `GET /v1/audit?limit=` -> audit log rows for the account
- `GET /health`

## Replicas
- `POST /v1/replicas` `{name, train_video_url}`; `GET /v1/replicas`; `GET /v1/replicas/{rid}`
- A replica cannot be marked `ready` without a consent record (worker must call `safety.require_consent(replica_id)`). `POST /v1/videos` returns 409 until ready.

## Consent
- `POST /v1/replicas/{rid}/consent/challenge` -> `{challenge_id, phrase, expires_at}` (15 min, single use; phrase includes a random code)
- `POST /v1/replicas/{rid}/consent` `{challenge_id, speaker_name, audio_url, transcript}`. 422 if the transcript (case/punctuation-insensitive) does not equal the phrase; 410 if expired/used.
- `GET /v1/replicas/{rid}/consent` -> `{has_consent, records}`; `DELETE` revokes all records.

## Personas, knowledge, memories
- `POST /v1/personas` `{name, system_prompt, replica_id?, llm?, tts_voice?, knowledge?}`; `GET /v1/personas`
- `POST /v1/personas/{pid}/knowledge/text` `{title, text}`; `POST .../knowledge/upload` (multipart `file`, `title?`); `GET .../knowledge`; `DELETE .../knowledge/{did}`; `POST .../knowledge/search` `{query, k?}`
- `POST /v1/personas/{pid}/memories` `{conversation_id?, summary?, turns?}`; `GET .../memories?limit=`

## Conversations
- `POST /v1/conversations` `{persona_id}` -> 402 if out of credits; `POST /v1/conversations/{cid}/end` (deducts seconds)
- WebSocket `/v1/conversations/{cid}/stream?api_key=`; close codes 4401 bad key, 4404 not found, 4402 ended/no credits
- `GET /v1/playground` HTML test page (`?cid=&api_key=`)

## Videos
- `POST /v1/videos` `{replica_id, script}`; `GET /v1/videos/{vid}`. Scripts should be passed through `safety.moderate_or_raise` (not yet wired into resources.py; see below).

## Billing
- `GET /v1/billing/plans`, `GET /v1/billing/status`
- `POST /v1/billing/checkout` `{provider: stripe|razorpay, kind: topup|plan, sku, success_url?, cancel_url?}` -> `{provider, checkout_url, id}`. 501 `provider_not_configured` when keys are absent.
- `POST /v1/billing/webhooks/stripe` (header `Stripe-Signature`, event `checkout.session.completed`), `POST /v1/billing/webhooks/razorpay` (header `X-Razorpay-Signature`, event `payment_link.paid`). 400 bad signature, 503 secret not set. Credits are added once per event id.
- Env: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `MIRAGE_USD_INR` (default 85).

## Moderation
- `POST /v1/moderation/check` `{text}` -> `{allowed, reasons, classifier}`. Blocklist always; set `MIRAGE_MODERATION_OLLAMA_MODEL` for an extra Ollama classifier (falls back to blocklist-only if Ollama is down). Extra terms: `MIRAGE_BLOCKLIST=a,b`.

## Integration hooks for other modules (python)
- `app.safety.require_consent(replica_id)` before setting `Replica.status="ready"`.
- `app.safety.moderate_or_raise(script)` in `create_video` and persona creation.
- `app.billing.record_usage(session, acc, seconds, ref=f"conv:{cid}")` in `end_conversation` to populate the ledger.
- `app.safety.enforce_rate_limit(acc.api_key, limit, window)` (in-process only).

## SDKs
Python `sdk/python/mirage_sdk` (`Mirage(api_key, base_url)`), TypeScript `sdk/js` (`npm run build`), embed `sdk/embed/widget.js`.

## Safety and infra (safety-infra module)
- `POST /v1/replicas/{rid}/consent/audio` multipart `challenge_id`, `speaker_name`, `file` (webm/ogg/wav/mp3/m4a). Server transcribes, matches the phrase + code words, stores the audio (sha256) and compares the voice with the training video. -> consent record + `phrase_score`, `voice_score`, `voice_status` (`match|mismatch|skipped|unavailable|no_speech`). Errors: 422 `phrase_mismatch` (includes `heard`), 422 `voice_mismatch|voice_no_speech`, 503 `voice_unavailable` (enforce mode, model/video unavailable), 410 expired/used challenge, 413 too large. `POST /consent` (typed) returns 403 `typed_consent_disabled` in production.
- `GET /v1/replicas/{rid}/consent/{cid}/audio` (owner-only evidence download), `GET .../{cid}/verification`.
- `POST /v1/files/sign` `{path}` -> `{url, expires_in}`; `/v1/files/*` require `?exp=&sig=` in production. `output_url` in responses is already signed.
- `DELETE /v1/replicas/{rid}` and `POST /v1/account/delete-my-data` `{confirm:"delete-my-data"}`.
- Every route is rate limited: 429 `{detail, retry_after}` + `Retry-After`; bodies over `MIRAGE_MAX_BODY_BYTES` (JSON) / `MIRAGE_MAX_UPLOAD_BYTES` (multipart) get 413. See `.env.example`.

## Feature layer (backend-features module)
All endpoints need `x-api-key` unless marked public. New tables only (no existing column changed). Interactive spec: `/docs`.

### Conversations: options, transcripts, summary, memory
- `POST /v1/conversations` now also accepts optional `participant_id` (memory scope), `context` (extra instructions, 4000 chars), `variables` (`{"first_name":"Ravi"}`, rendered into greeting / system prompt / guardrails / context wherever `{{first_name}}` appears), `max_seconds` (hard cap; the WebSocket is closed with code **4408** and an `error` event "conversation time limit reached"), `language` (overrides the persona language). Bad options -> 422.
- Credits are also enforced mid-call: the stream closes with 4408 when the account's remaining credits run out.
- `GET /v1/conversations/{cid}/transcript?format=json|text` -> `{summary, turns:[{seq, role: user|assistant, text, t_ms, first_audio_ms, interrupted, created_at}]}`. Turns are persisted live (one row per user utterance and per agent reply; interrupted replies are kept and flagged). `first_audio_ms` is the voice pipeline's time to first audio for that reply.
- `GET /v1/conversations/{cid}/summary` -> `{summary, ready}`; `GET .../objectives`; `GET .../tool-calls`; `GET .../metrics` (404 until finalised).
- **End of conversation** (`POST /v1/conversations/{cid}/end`, a guest session ending, or the idle reaper): an LLM writes a 2-3 sentence summary (extractive fallback if the LLM is down), saved as a persona memory (`MemorySummary`) scoped to `participant_id`; a metrics row is stored; `conversation.ended` and `transcript.ready` webhooks fire. Idempotent. Memories (latest 3 for that participant) are injected into the system prompt of the next conversation unless `memory_enabled=false`.
- **Idle reaper**: a conversation whose WebSocket closed more than `MIRAGE_END_GRACE_S` (default 30) seconds ago and was never ended is ended automatically (credits charged exactly once, ledger row written, summary + webhooks).
- New WebSocket events (additive, old clients ignore them): `objective_completed {name, evidence, variables}`, `guardrail {name}`.

### Persona config
- `GET/PUT /v1/personas/{pid}/config` (PUT is a partial update): `language` (`en en-gb es fr hi it pt ja zh`, STT-only: `de ru ar ko tr nl bn ta te ur`, or `auto`), `greeting` (non-empty = agent speaks first; supports `{{vars}}`), `objectives:[{name, description, success_criteria, output_variables}]`, `guardrails:[{name, rule, forbidden_phrases}]`, `guardrail_fallback`, `memory_enabled`, `custom_llm:{base_url, model, api_key}`, `stt_model`.
- **Objectives**: open objectives are put in the system prompt; after every agent turn an LLM judge (strict JSON, runs in the background, never blocks audio) decides which are complete and extracts `output_variables`. Completion is stored, pushed as the `objective_completed` WS event and the `objective.completed` webhook; a final pass runs at the end of the conversation. Judge model = the persona's LLM (or a custom endpoint); set `MIRAGE_JUDGE_MODEL=qwen3:8b` to judge with a stronger local Ollama model than the voice model. Judging a small voice model's own transcript with a 1B model is unreliable: expect missed completions.
- **Guardrails**: `rule` text is injected into the prompt as hard rules; `forbidden_phrases` and the global moderation blocklist are enforced on the output clause by clause before it is spoken. On a hit the reply is replaced by `guardrail_fallback`, generation is cancelled, a `guardrail` WS event and `guardrail.triggered` webhook fire.
- **Custom LLM**: `custom_llm.base_url` = any OpenAI-compatible `/chat/completions` endpoint (vLLM, LM Studio, OpenAI, Groq, Together ...). `api_key` is write-only: stored encrypted (Fernet if `cryptography` is installed, otherwise an HMAC-SHA256 encrypt-then-MAC construction), never returned (`has_api_key` only). Key material: `MIRAGE_SECRET_KEY` env or `$MIRAGE_DATA/secret.key`. Omit `api_key` to keep it, send `""` to clear.
- **Tools** (function calling): `POST /v1/personas/{pid}/tools {name, description, parameters (JSON schema, type object), webhook_url, secret?, timeout_s?}`, `GET`, `DELETE .../tools/{tid}`. When the model calls a tool Mirage POSTs `{"tool", "arguments", "conversation_id", "persona_id"}` to `webhook_url` (header `Mirage-Signature` if a secret is set, same scheme as webhooks, signed over the exact JSON body), feeds the response back to the model and the model speaks the result. Up to 3 tool rounds per reply. Works with Ollama models that support tools (qwen3:8b verified) and with OpenAI-compatible endpoints. Calls are logged (`GET .../tool-calls`) and emit `tool.called`. Set `MIRAGE_BLOCK_PRIVATE_URLS=1` in production to refuse private/loopback webhook and tool URLs (SSRF).
- qwen3 personas automatically use `think=false` (otherwise thinking tokens eat the short voice budget).

### Webhooks
- `GET /v1/webhooks/events`; `POST /v1/webhooks {url, events:["*"]|[names], description}` -> includes `secret` (`whsec_...`, shown on create/rotate only); `GET /v1/webhooks`; `PATCH /v1/webhooks/{id}`; `POST .../rotate-secret`; `POST .../test`; `DELETE`; `GET /v1/webhooks/{id}/deliveries` (delivery log: status pending|delivered|failed, attempts, last_status_code, last_error); `POST /v1/webhooks/deliveries/{did}/retry`.
- Events: `conversation.started`, `conversation.ended`, `transcript.ready`, `objective.completed`, `tool.called`, `guardrail.triggered`, `replica.ready`, `replica.error`, `video.ready`, `video.error`, `video_batch.completed`.
- Body: `{"id":"evt_...","type":"conversation.ended","created_at":"...","data":{...}}`. Headers: `Mirage-Signature: t=<unix>,v1=<hex hmac_sha256(secret, "<t>.<raw body>")>`, `Mirage-Event`, `Mirage-Delivery-Id`, `Mirage-Event-Id`. Verify with `Mirage.verify_webhook(secret, body, header)` (python) / `verifyWebhook` (js); reject if the timestamp is older than 5 minutes.
- Delivery: DB-backed queue, any 2xx = success, retries after 10 s, 1 min, 5 min, 30 min, 2 h (6 attempts) then `failed`. The loop runs inside the API process (disable with `MIRAGE_WEBHOOK_LOOP=0`); events from the worker process (replica/video) are queued in the shared DB and delivered by the API process. Delivery is at-least-once: dedupe on `Mirage-Event-Id`.
- The older per-video `callback_url` of `/v1/video-jobs` still works.

### Voices and languages
- `GET /v1/voices?language=es` -> `{languages:[{code,name,stt,tts,default_voice}], voices:[{id, language, gender, accent, default_for_language}]}`; `GET /v1/languages`. Non-English personas use faster-whisper's multilingual model (`base`; `small` for Hindi/CJK/Arabic/etc., override with `MIRAGE_STT_MULTI_MODEL` or `config.stt_model`; models auto-download on first use) and Kokoro voices with the right espeak language (`tts_voice: "default"` picks the language default; an explicit voice id such as `ef_dora` always wins). `language: "auto"` lets Whisper detect the language per utterance and answers in it when Kokoro supports it.

### API keys
- `POST /v1/keys {name}` -> `{id, key, prefix}` (full key shown once; only a SHA-256 hash is stored); `GET /v1/keys` (prefixes, last_used_at, revoked_at; the signup key appears as `id: "legacy"`); `DELETE /v1/keys/{id}` revokes (401 immediately, also for WebSockets); `POST /v1/keys/legacy/rotate` replaces the signup key. Max 25 active keys.

### Analytics
- `GET /v1/analytics?days=30` -> `totals` (conversations, minutes, turns, tool_calls, interruptions), `conversations_per_day` (dense series with minutes), `first_audio_latency_ms {avg,p50,p95,samples}`, `top_personas`, `videos {total, by_status, per_day}`, `replicas`, `credits_seconds`. Latency comes from per-conversation metrics stored at the end of each conversation.

### Video features
- Template variables: `POST /v1/videos/template/preview {script_template, variables}` -> `{variables, missing, rendered}`.
- `POST /v1/video-jobs/bulk {replica_id, script_template, rows:[{...}], voice, callback_url}` (max 200 rows, each row must define every `{{variable}}`; 422 lists bad rows) -> batch `{id, total, counts, completed, items:[{video_id,row_index,status,output_url,variables,script}]}`.
- `POST /v1/video-jobs/translate {replica_id, script, languages:[...], source_language, include_original, voices:{es:"ef_dora"}, callback_url}`: the LLM (`MIRAGE_TRANSLATE_MODEL`, default `qwen3:8b`) translates per language, each variant is queued with that language's Kokoro voice. Variants are re-rendered, not time-aligned dubs. 502 if the LLM is unavailable.
- `GET /v1/video-batches`, `GET /v1/video-batches/{id}`; `video.ready/video.error` per video and `video_batch.completed` when every video of a batch is done.

### Shareable guest links
- `POST /v1/personas/{pid}/share {label, max_seconds=300, max_total_seconds=3600, max_sessions_per_hour=20, max_sessions_per_ip_hour=5, expires_in_hours}` -> `{token: "sh_...", url: ".../guest/<token>", ...}`; `GET /v1/personas/{pid}/share` (usage: `used_seconds`, `sessions_started`); `DELETE /v1/share/{token}` revokes.
- Public (the token is the credential, no API key): page `GET /guest/{token}` (static HTML reusing the playground audio/face JS, with a countdown); `GET /v1/guest/{token}/info`; `POST /v1/guest/{token}/conversations` -> `{conversation_id, max_seconds, ws_path}` (404 bad/revoked, 410 expired, 402 host out of credits, 429 per-link/per-IP/total-seconds limits, with `Retry-After`); WebSocket `/v1/guest/{token}/stream?cid=` (same protocol as the normal stream; closes with 4408 at the time limit). Guest minutes are charged to the owner's credits and counted against `max_total_seconds` (the cost cap includes seconds reserved by running sessions). The owner's API key is never exposed to guests.
