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

### Cloned voice (consent-gated)
A cloned voice is a **synthetic copy of a real person's voice**. Mirage only creates one for a replica that has an active, **voice-verified** consent record (`verified_by: "asr-phrase+voice-match"`; the dev-only typed consent is accepted outside production), only lets the owning account use it, re-checks consent on every synthesis call, and deletes the stored reference audio when the consent is revoked, the replica or account is deleted, or the voice is deleted. Every step is in `GET /v1/audit` (`voice.clone_requested|ready|failed|refused|used|fallback|deleted|revoked`).
- `POST /v1/replicas/{id}/voice {force?: false}` -> 202 `{status:"queued"...}`. 403 `{error:"voice_clone_refused"}` without qualifying consent, 409 if the replica is not ready or its video has no audio, 503 if disabled (`MIRAGE_VOICE_CLONE=0`). Asynchronous: the server extracts the cleanest 15-20 s of the training audio (denoise, trim silence), registers it, synthesizes a test sentence and measures quality. The worker does this automatically when a consented replica becomes ready (`MIRAGE_VOICE_CLONE_AUTO=0` to disable).
- `GET /v1/replicas/{id}/voice` -> `{voice_id:"clone:<replica_id>", status: none|queued|processing|ready|failed|revoked, cloned:true, engine, reference_seconds, reference_quality{snr_db,...}, similarity, wer, synth_rtf, usable_in_conversations, error, notice}`. `similarity` is the WeSpeaker cosine between the reference and the clone saying a test sentence (same model as consent verification; a non-cloned preset voice scores about -0.2..0.05, a different real person 0.0-0.4, the same person 0.8-0.9), `wer` the Whisper round-trip word error rate, `synth_rtf` seconds of compute per second of audio on this server. A clone below `MIRAGE_CLONE_MIN_SIMILARITY` (0.30) is rejected (`failed`).
- `DELETE /v1/replicas/{id}/voice` -> `{deleted:true}` removes the reference audio and the row (the replica and its consent are untouched).
- `POST /v1/replicas/{id}/voice/preview {text<=300 chars, language}` -> `audio/wav` (moderated, rate limited, header `X-Mirage-Synthetic-Voice: cloned`).
- Using it: persona `tts_voice: "clone:<replica_id>"` (validated: must be a ready voice of your own replica, else 404/409) for conversations; for videos pass `voice: "clone"` (or `"clone:<replica_id>"`, which must be the video's own replica) in `POST /v1/video-jobs` / bulk; `clone:<id>@es` selects the language of the text. Cloned voices are listed in `GET /v1/voices` with `cloned: true`. ANY failure (engine missing/crashed, consent revoked mid-call, unknown voice) falls back to the Kokoro voice automatically and writes `voice.clone_fallback` to the audit log.
- SDK: `create_voice / get_voice / delete_voice / preview_voice` (python), `createVoice / getVoice / deleteVoice` (js).

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

## Creative features (photo avatar, backgrounds, captions, formats, scenes)
All routes under `/v1`, auth with `x-api-key`. SDK: `create_photo_replica`, `set_background`, `upload_asset`, `render_video`, `video_creative` (python) / `createPhotoReplica`, `setBackground`, `uploadAsset`, `renderVideo`, `getVideoCreative` (js).
- **Photo avatar** `POST /replicas/photo {name, photo_url, idle_seconds=4 (2-8), head_motion=1 (0-2)}` -> replica (`awaiting_consent`); consent exactly as for video replicas (the typed/audio consent flow; the voice-match step is `skipped` because a photo has no voice). The worker validates the photo (exactly one face, >= 90 px wide, mouth closed, eyes open; errors say what to fix), animates it with LivePortrait into a closed-mouth idle clip with blinking + subtle head sway (about 2.3 s per rendered frame on an M1 Pro, ~2-3 min once) and stores it as the replica's idle/listening clip, so live Wav2Lip conversations and offline videos work unchanged. `GET /replicas/{id}/photo` -> `{status: queued|animating|ready|error, error, warnings, animate_s, idle_url}`. No voice is cloned: the preset voice is used.
- **Backgrounds** `POST /replicas/{id}/background` `{type: "color", color:"#101828"}` | `{type:"gradient", colors:[2-4], angle}` | `{type:"image", asset_id, blur?}` | `{type:"blur", radius}` | `{type:"none"}`; `GET`/`DELETE` same path. Applies to live conversations (segmentation runs once when the lip-sync service prepares the base clip, live fps unchanged) and to every offline video of the replica unless the video sets its own `background`.
- **Assets** `POST /creative/assets` multipart (`file`, `kind=background|logo`) or JSON `{url, kind}`; PNG/JPEG/WebP <= 15 MB. `GET /creative/assets`, `DELETE /creative/assets/{id}`. `GET /creative/options` lists formats, styles, transitions, limits.
- **Creative video** `POST /video-jobs/render {replica_id, script, voice, callback_url, format: "16:9"|"9:16"|"1:1", resolution: 480|720|1080, background, captions: {style: classic|bold|minimal|karaoke, accent}, logo: {asset_id, position, scale, opacity}, transition: cut|fade|dip|slide, transition_s, scenes: "paragraphs"|"single", thumbnail, restore: none|sr}`. Blank-line separated paragraphs become scenes (max 12), rendered with Wav2Lip and joined with the transition. 9:16 / 1:1 use a face-aware crop. Captions come from Whisper word timestamps of the generated speech, aligned to the script text. `POST /videos/scenes/preview {script}` shows the split.
- **Progress** `GET /jobs/video/{id}` -> `detail.progress {stage: tts|render, scene, scenes, percent}` while rendering; `GET /videos/{id}/creative` -> options, progress, `thumbnail_url`, `captions_url` (.srt). Extra files are fetched with signed links: `POST /files/sign {path}` works for `/v1/files/creative/videos/{id}/thumbnail.jpg`, `.../captions.srt` and `/v1/files/creative/replicas/{id}/idle.mp4`.
- `options` (same object as above, minus ids) is also accepted by `POST /video-jobs/bulk` and `/video-jobs/translate`. Plain `POST /video-jobs` keeps the old LivePortrait renderer unless the replica is a photo replica or has a default background, or `MIRAGE_VIDEO_ENGINE=wav2lip` is set.

## Templates, lead capture, widget, booking (templates-leads module)
All routes need `x-api-key` unless marked public. New tables only (`models_templates.py`, `models_leads.py`); no existing column changed. SDK: `list_templates / get_template / instantiate_template / list_leads / export_leads_csv / get_lead / delete_lead / create_lead / get_lead_capture / set_lead_capture / create_widget / list_widgets / update_widget / delete_widget / get_integrations / set_integrations / test_integration` (python) and the camelCase equivalents (js).

### Persona templates
- `GET /v1/templates?niche=` -> `{templates:[{id, name, niche, summary, version, language, suggested_llm, tools, objectives:[names], lead_fields, knowledge_docs, safety_notes}], niches}`. Ids: `b2b-saas-sales-demo`, `customer-support`, `clinic-patient-intake`, `online-tutor`, `real-estate-lead-qualifier`, `hr-onboarding-buddy`, `hospitality-concierge`, `interview-practice-coach`.
- `GET /v1/templates/{id}` -> everything above plus `persona_name, system_prompt, greeting, objectives[], guardrails[], guardrail_fallback, variables{name: default}, knowledge[{title, chars, text}], sample_questions, recommended_lead_required_fields`. 404 unknown id.
- `POST /v1/templates/{id}/instantiate` `{name?, variables?:{company_name:"Zenith"}, language?, llm?, tts_voice?, replica_id?, include_sample_knowledge=true, enable_lead_capture=true, booking_webhook_url?, booking_secret?, notify_webhook_url?, notify_secret?}` -> `{persona_id, template_id, persona{id,name,llm,tts_voice,replica_id,system_prompt}, config{language, greeting, objectives[names], guardrails[names]}, knowledge_docs:[{id,title,chunks}], tools:[...], lead_capture{enabled, required_fields}, warnings[], next_steps[]}`. One call creates the persona, its config (greeting, objectives, guardrails, language), the lead-capture settings and ingests the sample knowledge. `variables` replace `{{placeholders}}` in the prompt/greeting/guardrails (unknown names -> 422). **The sample knowledge documents are titled `SAMPLE - ...` and describe a FICTIONAL company with fake data (example.com emails, 555-01xx phones): replace them with your own (`POST /v1/personas/{id}/knowledge/text`, then `DELETE` the sample docs).** `warnings` repeats this (and the health/safety note of the clinic template). 404 template/replica, 422 bad variables/language/url.
- Default model is `ollama/qwen3:8b` (best tool-calling in our evals); `ollama/llama3.2:3b` is lighter but calls tools less reliably.

### Lead capture
- Built-in tool **`capture_lead`** `{name, email, phone, company, interest, notes, consent}` is added to a persona's tools automatically when lead capture is enabled for it (templates enable it; other personas opt in with `PUT .../lead-capture`; personas without settings are unchanged). Validation: email format (spoken "x at y dot com" is normalised), phone 7-15 digits (stored as `+digits`), `name` required (configurable), at least one of email/phone, `consent=true` required by default (otherwise the tool tells the model to ask permission first). One lead per conversation: later calls merge into it. The agent is told to ask once, never push, say how the data is used first, one detail at a time. A persona tool with the same name wins. **Safety net**: local models often promise "I'll save your details" without calling the tool, so (a) when an objective completes with a name plus email/phone and (b) at the end of the conversation (`finalize_conversation`) Mirage extracts the details from the transcript with the judge model and stores the lead (`source: objective|sweep`, only when the visitor clearly agreed to be contacted; every value must literally appear in the visitor's own words, phone numbers are taken from their text, not from the model's rendering).
- `GET/PUT /v1/personas/{pid}/lead-capture` `{enabled, required_fields:[name,email,phone,company,interest,notes], require_consent, disclosure}` (PUT is partial; `disclosure` is stored with each lead as `consent_text`).
- `GET /v1/leads?persona_id=&conversation_id=&since=&until=&q=&consent=&limit=50&offset=0` -> `{items:[lead], total, limit, offset}`, newest first. `since/until` accept `YYYY-MM-DD` or ISO 8601; `q` searches name/email/phone/company/interest/notes (case-insensitive substring). Lead = `{id, conversation_id, persona_id, name, email, phone, company, interest, notes, extra{}, consent, consent_text, source: tool|api, created_at, updated_at}`.
- `GET /v1/leads/export.csv` (same filters, up to 50 000 rows; columns `id, created_at, persona_id, persona_name, conversation_id, name, email, phone, company, interest, notes, consent, source`; cells starting with `= @ + -` are prefixed with `'` against spreadsheet formula injection).
- `GET /v1/leads/{id}`, `DELETE /v1/leads/{id}` (permanent erasure), `POST /v1/leads` `{persona_id, conversation_id?, name, email, phone, company, interest, notes, consent=true}` (create from your own forms; same validation; 422 on invalid).
- Webhook events `lead.captured` (first capture of a conversation) and `lead.updated`: `data = {lead:{...}, conversation_id, persona_id}`. Leads are deleted with `DELETE /v1/account/delete-my-data` (the tables carry `account_id/persona_id/conversation_id`, which `data_deletion.py` purges by metadata).
- Analytics: `GET /v1/analytics` gains `leads {total, per_persona:[{persona_id, name, leads, conversations, conversion_rate}]}` and `objectives {per_persona:[{persona_id, name, objective, conversations, completed, completion_rate}]}` (additive).

### Embed widget ("Talk to us" button)
One tag, no API key: `<script src="https://API/widget.js" data-token="sh_..." async></script>`. See `docs/WIDGET.md`.
- `POST /v1/personas/{pid}/widget` `{allowed_domains:[], label="Talk to us", color="#6d5efc", position: bottom-right|bottom-left, greeting="", language="", max_seconds=300, max_total_seconds=3600, max_sessions_per_hour=20, max_sessions_per_ip_hour=5, expires_in_hours?}` creates a guest share token + widget settings -> `{token, persona_id, allowed_domains, label, color, position, greeting, language, script_url, frame_url, share_url, snippet, limits{...}, created_at}`.
- `GET /v1/widgets?persona_id=`, `GET /v1/widgets/{token}`, `PUT /v1/widgets/{token}` (partial update; also attaches settings such as allowed domains to an existing share token), `DELETE /v1/widgets/{token}` (revokes the token at once).
- **Allowed domains**: `allowed_domains` entries are `example.com` (any port), `*.example.com` (subdomains only), `localhost:8000`, `https://shop.example.com`; `[]` = any site. Enforced (1) by `Content-Security-Policy: frame-ancestors` on the iframe page (browsers refuse to frame it elsewhere) and (2) by `POST /v1/guest/{token}/conversations` and the guest WebSocket, which return 403 / close 4403 when the browser `Origin` is neither an allowed site nor the API's own origin. This stops other websites; it cannot stop a script that forges headers (the share-token cost caps and per-IP limits remain the backstop). The plain share link `/guest/{token}` keeps working for everyone.
- `POST /v1/guest/{token}/conversations` now accepts an optional JSON body `{language}` (used by the widget `data-language`).
- Public: `GET /widget.js` (the script, 5 min cache), `GET /widget/frame/{token}[?lang=]` (the iframe page; allowed to be framed, unlike the rest of the API).

### Booking and notification tools (optional)
- `PUT /v1/personas/{pid}/integrations` `{booking_webhook_url, booking_secret, notify_webhook_url, notify_secret}` (secrets write-only, `""` clears; a URL enables the tool), `GET` same path (`{booking:{enabled, webhook_url, has_secret}, notify:{...}}`), `POST .../integrations/test` `{which: booking|notify}` -> `{ok, response}` (sends a call marked `arguments.test=true`).
- Tool **`book_meeting`** `{name, email, preferred_time, timezone?, topic?, duration_minutes?}` and **`send_notification`** `{subject, message, urgency?, contact?}` POST `{"tool","arguments","conversation_id","persona_id"}` to your URL (HMAC `Mirage-Signature` header like webhooks when a secret is set; any 2xx = success). The agent never claims the meeting is confirmed. Connect Cal.com / Zapier / Make / n8n: see `docs/WIDGET.md`.
