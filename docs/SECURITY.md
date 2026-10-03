# Mirage security model

Honest scope: this describes what the code does today and what it does not. No external audit, no SOC 2.

## Threat model
| Threat | Mitigation in code | Residual risk |
|---|---|---|
| **Deepfake of a person who did not consent** (core abuse case) | A replica cannot become `ready` without a consent record (`safety.require_consent`). Consent = the person records their voice reading a random phrase with 3 random code words (single use, 15 min). Server transcribes (faster-whisper), fuzzy-matches the phrase (score >= 0.8, all code words in order, tolerant to ASR slips but never to a different code word), stores the audio + sha256, and compares the voice to the one in the training video (WeSpeaker ResNet34 embedding, cosine >= 0.50). | See "Consent limits" below. |
| Voice/face used to say harmful things | Blocklist + optional LLM classifier on scripts, checked at the API and again in the worker. | Classifier is imperfect (9/9 on a small sample set; not a benchmark). Real-time conversation output is not moderated frame by frame. |
| Guessing/scraping file URLs | `/v1/files/*` need an HMAC-signed `exp`+`sig` link (default TTL 1 h, `POST /v1/files/sign` after an owner check; `output_url` fields in API responses and webhook payloads are signed automatically). Unsigned access exists only in dev or with `MIRAGE_ALLOW_PUBLIC_FILES=1`. A wrong signature never falls back to public access. | Anyone who holds a link can use it until it expires. Revoke everything by rotating `MIRAGE_SECRET_KEY`. |
| API abuse / cost exhaustion | Rate-limit middleware (per IP for everything, per API key, tighter per-route: signup, video creation, checkout, conversations, replica create, moderation), 429 with `Retry-After`; request size caps (413); credit checks. | In-process counters: per worker, reset on restart. WebSockets are not rate limited per message. A botnet with many IPs can sign up repeatedly (no email verification/captcha). |
| Browser attacks on the API | Security headers (nosniff, DENY framing except the embeddable playground, no-referrer, CSP `default-src 'none'` on JSON, HSTS in production, `no-store` on JSON). CORS is an explicit allow-list (production refuses `*`). | The dashboard stores the API key in `localStorage`: any XSS steals it. Keys are shown/stored in plaintext in the DB (hashing is planned in another module). The WebSocket takes `?api_key=` in the URL, which can land in proxy logs. |
| SSRF via training-video / callback URLs | Production consent-time fetch blocks private/loopback/link-local targets (every redirect hop checked), caps size at 300 MB, refuses local paths. | The worker's own downloader (`jobs.fetch_video`) and webhook delivery are separate code; verify they apply the same guard before exposing to untrusted users. DNS rebinding between check and fetch is not handled. |
| Secrets in logs | Settings module never logs values; `settings.redact()` masks `mk_/sk_/whsec_` tokens in error paths we wrote. Production boot fails on missing secret. | Other modules' logging and uvicorn access logs (query strings!) are not scrubbed. |

## Consent limits (read before relying on it)
1. **Not liveness detection.** Someone who owns a recording of the real person's voice (or a good voice clone of them) and can play it into the microphone can pass: the check proves the *audio* says the random phrase in a voice that matches the video, not that the human is present. The random phrase makes pre-recorded replay hard, not live TTS-cloning attacks.
2. **Face is not checked.** Voice match compares audio only. A video of person A with person B's voice track would pass if B consents. There is no face-to-voice binding.
3. The voice model is a general speaker verifier. Our test: same speaker scored 0.82-0.91, different (synthetic) speakers 0.01-0.41, one human-vs-TTS pair 0.04-0.14. Real-world noise, codecs, illness and short clips shift scores; the 0.50 threshold is untuned on real consent data. Expect false rejects on bad audio and some false accepts on close relatives/impersonators.
4. English ASR only (`base.en`). A mispronounced name may lower the phrase score but code words decide.
5. The reference voice comes from the first 60 s of the training video: if that video has several speakers or music, scores are unreliable (production enforces, so it would reject; dev only warns).
6. The typed-phrase path (`POST /consent`) is a dev convenience and is disabled in production (`403 typed_consent_disabled`).
7. Stored consent audio is private (owner-only endpoint) and kept until the replica is deleted. Revoking consent marks records revoked; `DELETE /v1/replicas/{id}` removes everything.

## Data retention and deletion
- `DELETE /v1/replicas/{id}`: deletes the replica row, its videos (rows + MP4s), the replica folder (source video copy, face, voice reference), consent recordings and verification rows, consent challenges, job rows, and every other table that references the replica or its videos (discovered from the schema, so future tables are covered). Personas survive with `replica_id = null`.
- `POST /v1/account/delete-my-data` `{"confirm":"delete-my-data"}`: deletes everything owned by the account (replicas, videos, personas, knowledge, conversations, API keys, ...) and the account row, then the key stops working.
- **Kept on purpose:** ledger entries and payment events (financial records), the audit log, and a `datadeletion` tombstone (ids and counts only). Document your own retention policy; a legal retention period may apply.
- Not covered: database backups and copies you made; LLM provider logs if you configure a hosted LLM; Ollama keeps no data. Webhook receivers keep what they received.

## Operator checklist
1. `MIRAGE_ENV=production`, strong `MIRAGE_SECRET_KEY`, explicit `MIRAGE_CORS_ORIGINS`.
2. TLS in front; `MIRAGE_TRUST_PROXY=1` only if behind your proxy.
3. Keep port 8100 (lipsync), 11434 (Ollama), and the DB private.
4. Download the speaker model (`make models`); production returns 503 `voice_unavailable` for consent without it (fail closed).
5. Tune `MIRAGE_VOICE_MATCH_THRESHOLD` on your own consenting users before launch.
6. Back up DB + data together; test a restore.
