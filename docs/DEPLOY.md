# Self-hosting Mirage

Docker files below were written carefully but **never built or run** (Docker is not installed on the dev machine). Everything else in this guide (venv install from `requirements.txt`, production-mode config checks, tests) was run.

## 0. Pick a mode
`MIRAGE_ENV=dev` (default): convenient. Unsigned `/v1/files/*`, typed-phrase consent allowed, voice mismatch only warns, CORS defaults to `http://localhost:3000`.
`MIRAGE_ENV=production`: the API **refuses to boot** without `MIRAGE_SECRET_KEY` (16+ chars) and an explicit `MIRAGE_CORS_ORIGINS` (not `*`). It also turns off unsigned files and typed consent, enforces voice match, uses stricter signup limits and sends HSTS. Check without booting: `make check-prod`.
Every variable is documented in `.env.example`; the app reads real environment variables first, then a `.env` file (`./.env` or repo root). Empty values count as unset.

## 1. Single machine, no Docker
```
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt   # requirements.lock = exact full freeze
make -C .. models                       # 26 MB speaker-verification model (Apache-2.0); also needs models/kokoro-v1.0.onnx + voices-v1.0.bin
export MIRAGE_ENV=production MIRAGE_SECRET_KEY=... MIRAGE_CORS_ORIGINS=https://app.example.com
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
.venv/bin/python ../workers/run_worker.py --poll 3        # separate process: trains replicas, renders videos
```
Needs `ffmpeg` on PATH. SQLite: run exactly one API process per DB file (the rate limiter and webhook loop are in-process). More workers needs Postgres (`MIRAGE_DB_URL`) and a shared limiter (not built).
Dashboard: `cd web && NEXT_PUBLIC_API_URL=https://api.example.com npm ci && npm run build && npm start` (the API URL is baked in at build time).

## 2. Docker Compose (UNTESTED)
```
cp .env.example .env     # set MIRAGE_SECRET_KEY, MIRAGE_CORS_ORIGINS, PUBLIC_API_URL ...
docker compose -f infra/docker-compose.yml up -d --build
```
Services: `backend` (API :8000), `web` (:3000), `worker` (replica/video jobs, GPU image), `lipsync` (Wav2Lip :8100, GPU), `ollama`. Ports bind to 127.0.0.1; expose them through the reverse proxy. Model files are mounted from `./models`, `workers/Wav2Lip/checkpoints`, `workers/LivePortrait/pretrained_weights`; they are not in the images. `worker` and `lipsync` and `ollama` request an NVIDIA GPU (needs the NVIDIA container toolkit); delete the `deploy:` blocks to try CPU, where faces will not be real time. Pull Ollama models once: `docker compose exec ollama ollama pull llama3.2:1b && ... pull qwen3:8b`.
Known gaps in the compose setup: no health-gating between services, the GPU image downloads nothing at build time (weights are yours to supply), and LivePortrait's own dependency set is not verified in the image.

## 3. Optional rented GPU box
Run only `worker` + `lipsync` there (`infra/Dockerfile.gpu`). The worker needs the **same database and `/data` files** as the API: with SQLite that means the same machine or a network filesystem (fragile). Practical options: (a) put everything on the GPU box; (b) Postgres + object storage (not implemented: file paths are local). Point `MIRAGE_LIPSYNC_URL` at the box over a private network or WireGuard; never expose port 8100 publicly (it has no auth).

## 4. TLS / reverse proxy
`infra/Caddyfile` is a two-host example (automatic Let's Encrypt, WebSocket pass-through, 30 MB body cap). With nginx: proxy_pass + `proxy_set_header Upgrade/Connection` for `/v1/conversations/.../stream`, and `client_max_body_size 30m`. Behind your own proxy set `MIRAGE_TRUST_PROXY=1` so rate limits use the real client IP (never set it when the API is directly reachable: clients could spoof `X-Forwarded-For`).

## 5. Ollama (LLM + moderation classifier)
`ollama serve`, `ollama pull llama3.2:1b qwen3:8b`; `OLLAMA_URL`, `MIRAGE_MODERATION_OLLAMA_MODEL` (empty = blocklist only).

## 6. Payments
Env vars in docs/API.md; webhook URLs `https://HOST/v1/billing/webhooks/stripe` and `/razorpay`. Not tested against live providers.

## 7. Backups and retention
Back up the DB file and `MIRAGE_DATA` together (consent recordings are evidence). Deletion: `DELETE /v1/replicas/{id}` and `POST /v1/account/delete-my-data` (see docs/SECURITY.md). Rotate `MIRAGE_SECRET_KEY` to invalidate every signed file link at once.

## 8. Tests and checks
`make test` (60 tests incl. a real Whisper + speaker-model consent test; it is skipped if models are missing), `make lint` (compileall, pyflakes if installed, tsc).

---

## 9. Database migrations (Alembic) and Postgres
Schema changes are versioned in `backend/alembic/versions/`. From `backend/`:
```
python -m app.migrate status        # {'current': '0001', 'head': '0001', 'state': 'up-to-date'}
python -m app.migrate upgrade       # fresh DB: creates everything; old create_all() DB: verifies/stamps, adds missing tables
python -m app.migrate check         # exit 1 when behind (use in CI / before deploy)
alembic revision --autogenerate -m "add column x"   # after changing a model; review the generated file!
```
Production: set `MIRAGE_AUTO_CREATE=0` (the API then never calls `create_all`) and run `upgrade` as a release step. In dev the API still creates missing tables itself. At startup the API logs a WARNING (`DATABASE SCHEMA IS BEHIND`) when the database is behind the code; `/health/deep` reports it under `checks.migrations`. `tests/test_migrations.py` fails if a model change has no migration (`schema_diff` must be empty).
Existing sqlite file made by an older build: `python -m app.migrate upgrade` compares it to the models; identical -> stamped at head, tables missing -> created then stamped, column differences -> stamped at the baseline and newer revisions applied (column changes on old files need a hand-written revision: SQLite cannot always alter in place, `render_as_batch` is on).
**Postgres**: `pip install "psycopg[binary]"`, then `MIRAGE_DB_URL=postgresql+psycopg://user:pass@host:5432/mirage` and run `python -m app.migrate upgrade`. The sqlite-only `check_same_thread` option is only sent to sqlite URLs; other databases get `pool_pre_ping` and a pool (`MIRAGE_DB_POOL`, default 10). With Postgres several API processes and several workers are possible for the database part, but the rate limiter, the webhook loop and the conversation runtime registry are still in-process (see "Known gaps").
**What was tested without a Postgres server**: every table and index compiles to valid PostgreSQL DDL (`test_ddl_compiles_for_postgresql`), no raw SQL exists in the app (all queries go through SQLAlchemy), engine options are dialect-aware, no 32-bit-overflow-prone epoch columns. **NOT tested**: an actual Postgres connection, `alembic upgrade` against Postgres, query behaviour differences (case-sensitive `LIKE`, timestamp handling, `IntegrityError` races under real concurrency), migration speed. Run the e2e suite against a Postgres once before trusting it.

## 10. Object storage (S3-compatible)
`MIRAGE_STORAGE=local` (default) keeps every path and behaviour exactly as before: the data dir is the store. `MIRAGE_STORAGE=s3` makes S3 (AWS, Cloudflare R2, MinIO, Backblaze B2) the durable home of replica faces + source/voice refs, rendered videos, consent recordings and listening clips:
```
pip install boto3
export MIRAGE_STORAGE=s3 MIRAGE_S3_BUCKET=mirage-prod MIRAGE_S3_PREFIX=prod MIRAGE_S3_REGION=eu-west-1
# S3-compatible (R2/MinIO): also MIRAGE_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com ; credentials via AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY
```
Flow: the worker writes to its local working dir and uploads when a job finishes (`storage.publish`); the API pulls from the bucket on demand (`ensure_local`) and serves files by verifying the Mirage signed URL first and then answering **307 -> a presigned bucket URL (max 5 min)**, or streaming through the API with `MIRAGE_S3_REDIRECT=0`. A GPU worker on another machine therefore no longer needs a shared filesystem; it needs the same database (Postgres) and bucket credentials. Give the bucket a lifecycle/CORS policy (GET from your dashboard origin if the browser fetches videos with `fetch()`; `<video src>` needs none) and keep it private. Deleting a replica/account deletes the bucket objects too. The lip-sync service still reads the replica's local files (it calls `ensure_local` equivalents only on the worker path): run it where the worker runs.
**Tested**: local backend (unchanged paths), S3 backend against a fake client and against `moto` (in-process S3), presign + redirect + signature-before-redirect, key traversal rejection. **NOT tested**: a real AWS/R2/MinIO bucket, multipart upload of very large videos, IAM policy minimums (needs `s3:GetObject/PutObject/DeleteObject/ListBucket` on the prefix).

## 11. Observability
- Logs: `MIRAGE_LOG_FORMAT=json` (default in production) emits one JSON object per line with `request_id`; every response carries `X-Request-ID` (an inbound one is honoured). The access log has method, route template, status, latency and a hashed key id only: never keys, query strings, bodies or IPs; free-text messages go through `settings.redact`.
- `GET /metrics` (Prometheus text). Set `MIRAGE_METRICS_TOKEN`; scrape with `Authorization: Bearer <token>` (or `?token=`). Unset: open in dev, **disabled (403) in production**. Series: `mirage_http_requests_total{method,route,status}`, `mirage_http_request_duration_seconds` (histogram), `mirage_conversations_active`, `mirage_websocket_connections`, `mirage_worker_queue_depth{kind}`, `mirage_videos_rendering`, `mirage_webhook_deliveries{status}` (failed = retries exhausted), `mirage_first_audio_window_ms` (p50/p95 over the last hour from stored transcript turns), `mirage_worker_heartbeat_age_seconds`, plus an in-process `mirage_first_audio_seconds` histogram that the conversation runtime may feed via `metrics.observe_first_audio`.
- `GET /health` stays the cheap liveness probe. `GET /health/deep` checks db (critical: 503 when down), migrations, Ollama, lip-sync, worker heartbeat and storage; non-critical failures report `status: degraded`. The worker heartbeat is touched on every `run_worker` poll, so a dead worker shows as `stale`.
- Operator CLI (`backend/`): `python -m app.admin stats | accounts | account <id|email> | grant <id|email> --minutes 60 | plan <id|email> pro | usage <id> | billing-reset | migrate status`. Keys are never printed.
Not built: tracing (OpenTelemetry), alert rules (suggested: `mirage_worker_queue_depth > 5 for 10m`, `mirage_worker_heartbeat_age_seconds > 60`, 5xx rate, `mirage_webhook_deliveries{status="failed"}` increasing).

## 12. Billing enforcement (credits, overage, monthly reset)
Credits are seconds on `Account.credits_seconds` (a plan purchase or top-up adds seconds). Enforcement:
- Starting a conversation or queuing a video returns **402** when the account cannot cover it. A conversation's hard time cap is always `min(requested max_seconds, credits + overage headroom)`; the WebSocket watchdog and guest links use the same allowance.
- **Overage** (`PUT /v1/billing/overage {"enabled": true, "cap_cents": 500}`): paid plans only (Starter 20 c/min, Pro 15 c/min; Free has none). Usage beyond the balance is metered per second in millicents (`ceil(seconds * cents_per_min * 1000 / 60)`), summed per calendar month (UTC), and **never billed beyond the cap**; sessions are shortened so they cannot outrun it. A cap > 0 is required to enable it. `GET /v1/billing/overage`, `GET /v1/billing/status` (`available_seconds`) and `GET /v1/usage/report?period=YYYY-MM` (seconds by kind, daily series, overage seconds/spend/remaining) expose it. **This records and caps overage charges; it does not charge a card.** Turning overage cents into a Stripe/Razorpay invoice or usage record is not built (those provider interfaces are untouched and still untested live).
- Videos are billed when the worker starts the render (so bulk/translate videos are covered too) at an estimate of narration length (2.5 words/s, min 2 s) and refunded automatically if the render errors.
- **Monthly reset** (`billing.run_monthly_reset`, run hourly by an API background loop unless `MIRAGE_BILLING_LOOP=0`, or `python -m app.admin billing-reset` from cron): plans are one-time monthly purchases, so a plan bought in an earlier calendar month lapses to `free` and its unused allowance expires (plan credits are consumed before top-ups; top-ups never expire). No proration. Idempotent.

## 13. Team workspaces
`POST /v1/workspaces`, invites (`POST /v1/workspaces/{id}/invites` returns a one-time token; the invitee's account email must match; `POST /v1/workspaces/invites/accept`), roles owner/admin/member. A member calls the API with their own key plus `X-Workspace: <id>` and then acts as the owner's account (shared personas, replicas, credits): admin = everything except billing/keys/account deletion/workspace management, member = read-only plus conversations. Without the header nothing changes. Not covered: the WebSocket stream and guest endpoints authenticate with the key directly and ignore workspaces; no UI yet.

## 14. End-to-end suite
`make e2e-smoke` (backend + dashboard only, ~1 min, no models) and `make e2e` (full: replica + voice consent through a fake microphone fed Kokoro speech, worker training, persona + knowledge, live conversation with lip-sync frames, video render, every route at 1440/390, guest conversation, webhook delivery with HMAC check). Isolated ports + temp DB; logs and failure screenshots land in `e2e/artifacts/` (gitignored). `make check` = tsc + pytest + smoke. See `e2e/README.md`.
