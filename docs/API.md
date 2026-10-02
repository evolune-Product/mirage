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
