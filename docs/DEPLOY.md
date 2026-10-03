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
