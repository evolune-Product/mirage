# Self-hosting Mirage

## 1. API server
```
cd backend
python3 -m venv .venv && .venv/bin/pip install fastapi sqlmodel uvicorn httpx   # no requirements.txt exists yet
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Storage is SQLite (`backend/mirage.db`, relative to the working directory). Run exactly one process per DB file unless you move to Postgres (db.py would need an engine URL change). The rate limiter is in-process, so it is per worker.
Put it behind HTTPS (Caddy/nginx); the playground uses `wss` automatically on https.

## 2. Ollama (LLM, optional moderation classifier)
```
ollama serve &  ollama pull llama3.2
export OLLAMA_URL=http://localhost:11434
export MIRAGE_MODERATION_OLLAMA_MODEL=llama3.2   # optional
```

## 3. Worker (replica training / video render)
Run the worker module from `workers/` (owned by another module; check its README for the exact command). Before it sets a replica `ready` it must call `from app.safety import require_consent; require_consent(replica_id)`.

## 4. Optional GPU worker
Rent a GPU box (any provider), run the same worker there pointed at the API's database/queue endpoint. Without a GPU, voice-only conversations still work locally; rendered faces will not be real-time on CPU. See docs/UNIT_ECONOMICS.md for cost scenarios.

## 5. Payments (optional)
Set the env vars in docs/API.md. Register webhook URLs `https://HOST/v1/billing/webhooks/stripe` (event `checkout.session.completed`) and `.../razorpay` (event `payment_link.paid`) in each dashboard, using the same secret as `*_WEBHOOK_SECRET`. Without keys, checkout returns 501 and webhooks 503. Not tested against live provider endpoints.

## 6. Tests
`cd backend && .venv/bin/python -m pytest -q`
