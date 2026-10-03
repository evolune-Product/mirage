# Overnight report: safety-infra (agent "safe")

## What was built
1. **Real consent verification.** `POST /v1/replicas/{id}/consent/audio` (multipart). Server: ffmpeg decode, faster-whisper `base.en`, fuzzy phrase match (>= 0.8, code words in order, one-char ASR slips tolerated, amber vs ember never confused, glued words like "cederemberpixel" handled), stores audio + sha256 + a `consentverification` row (`models_safety.py`), speaker check with the WeSpeaker ResNet34 ONNX model (`voiceprint.py`, numpy Kaldi-style fbank + onnxruntime, no torch). Modes `enforce|warn|off` (enforce in production). Typed path kept, gated by `MIRAGE_ALLOW_TYPED_CONSENT` (dev on, production off). Dashboard: `web/components/ConsentRecorder.tsx` (MediaRecorder, preview, re-record, shows "We heard ..." on failure), wired into the replicas page.
2. **Signed expiring URLs.** `signing.py` HMAC-SHA256 over path+expiry. `/v1/files/*` verify; unsigned only in dev/flag. `POST /v1/files/sign` (owner check). `output_url`/`face_url` JSON fields are signed by the middleware; realtime `face_url`, webhook payload `output_url` (24 h) and the dashboard face thumbnail were updated. One existing test assertion in `test_jobs.py` was adjusted for the query string.
3. **Hardening middleware** (`hardening.py`, installed in `main.py` before CORS so 429/413 keep CORS headers): per-IP + per-key + per-route limits (env `MIRAGE_RL_*`), 429 + Retry-After, body size caps (declared and streamed), security headers, HSTS in production. `safety.RateLimiter` gained `check_ex`, pruning, `reset`.
4. **Config.** `settings.py` (lazy env reads, production validation, `redact`), `envfile.py` (.env loader, wired in `app/__init__.py`), `.env.example`.
5. **Deletion.** `DELETE /v1/replicas/{id}`, `POST /v1/account/delete-my-data` (`data_deletion.py`, schema-driven so other agents' new tables are covered; ledger/audit kept).
6. **Deploy.** `infra/` Dockerfiles (backend, web, GPU), compose, Caddyfile, `requirements.txt` (pinned) + `requirements.lock` + `requirements-dev.txt`, `Makefile`, `docs/DEPLOY.md`, `docs/SECURITY.md`, API.md section.

## Verified
- `pytest`: 60 passed in the shared venv and in a **fresh venv built from `requirements.txt`** (Python 3.14.7).
- New tests (`tests/test_safety_infra.py`, 17): phrase matching, signing, 403/200 file access, user strings not rewritten, 429/Retry-After per key/IP, CORS on 429, size limit, headers, production validation, typed consent flag, consent audio with mocked ASR (nothing stored on failure, single use, owner-only evidence), **consent audio with real Whisper + real speaker model** (right words/wrong voice rejected with score < 0.5; right voice accepted > 0.5), replica + account deletion.
- **Real browser E2E** (Playwright, fake mic fed Kokoro speech; `scratchpad/pw/safe_e2e.mjs`): wrong voice rejected, wrong words rejected with "We heard ...", correct phrase+voice accepted (voice score 0.859, `verified_by=asr-phrase+voice-match`), replica moves to the training queue.
- Production-mode smoke on a separate port: boot refused without secret/CORS; typed consent 403; HSTS present; signup 429 after 5.
- Voice model separation measured on Kokoro voices + a human video: same speaker 0.82-0.91, different 0.01-0.41.
- `tsc --noEmit` clean for the web app. Compose YAML parses (ruby psych).

## Not verified / limits
- **Docker files, compose and Caddyfile are untested** (no Docker). GPU image assumes the pytorch base tag exists and that LivePortrait deps are satisfied; not checked.
- Speaker threshold 0.50 is not tuned on real consent recordings; human-microphone audio was not tested (only Kokoro speech through Chrome's fake mic, and one human video for the embedding test). Consent is not liveness detection and does not bind face to voice (see SECURITY.md).
- Rate limiting is in-process (per worker, resets on restart); WebSocket messages are not limited.
- SSRF guard covers the consent-time download only; `jobs.fetch_video` and webhook delivery were not changed by me.
- The marketing `/security` page still says consent "does not yet match the voice"; that is now outdated (owned by the marketing agent).
- Other agents' code that emits file URLs outside JSON `output_url`/`face_url` keys will produce unsigned links (break in production); I only found and updated realtime, jobs webhook and the dashboard face.
- Existing `localStorage` API-key storage and plaintext key storage are unchanged (key hashing belongs to backend-features).
- Left the shared servers (8000/8100/3000) alone. Downloaded `models/wespeaker_resnet34_lm.onnx` (26 MB) into the repo's models dir.
