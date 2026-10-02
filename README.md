# Mirage

Open-core Conversational Video Interface (CVI) platform: real-time face-to-face AI agents, digital-twin replicas, and programmatic video generation, built on free/open-source components and designed for cost-optimized GPU use.

## Feature parity map (Tavus -> Mirage)

| Tavus | Mirage module | Free stack |
|---|---|---|
| Conversational Video Interface | `backend/app/routers/conversations.py`, `pipeline/` | LiveKit (self-host), faster-whisper, Ollama/any LLM, Kokoro/Piper TTS |
| Replicas (digital twins) | `routers/replicas.py`, `workers/` | LivePortrait / MuseTalk on rented or local GPU |
| Video generation API | `routers/videos.py` | Same renderer, batch queue |
| Personas, knowledge base, memory | `routers/personas.py` | SQLite/Postgres + local embeddings |
| Turn-taking, perception | `pipeline/turn_taking.py` | Silero VAD |
| API keys, usage, billing | `auth.py`, `routers/usage.py` | Stripe/Razorpay later |

## Cost strategy
- Every stage is a pluggable provider (`pipeline/providers.py`): swap free local models for paid APIs per customer tier.
- Stream everything (STT -> LLM -> TTS -> render) to hit low latency without bigger GPUs.
- Meter usage per second so margins are visible from day one.

## Run
```
cd backend && .venv/bin/uvicorn app.main:app --reload
.venv/bin/pytest
```

## Honest limits
Renderer quality and sub-second latency depend on GPU-hosted open models; this repo provides the platform, orchestration and provider interfaces. See docs/ROADMAP.md.
