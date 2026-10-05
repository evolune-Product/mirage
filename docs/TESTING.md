# End-to-end test results (Oct 2-3 2026, M1 Pro, real browser via Playwright)

Run through the actual dashboard UI against the live backend, Whisper, Kokoro, Ollama, LivePortrait.

| Check | Result |
|---|---|
| Signup -> API key stored -> dashboard | pass |
| Replica from URL (downloaded over HTTP) starts `awaiting_consent` | pass |
| Wrong consent phrase rejected, correct phrase accepted | pass |
| Worker trains replica only after consent | pass |
| Persona with replica + knowledge document | pass |
| Voice: fake mic speaks question -> STT -> retrieval -> LLM -> TTS answer ("$19 per month") | pass, ~8 s click-to-answer incl. connect |
| Replica face shown and animated while agent speaks | pass (static face + pulse, not lip-sync) |
| Video generation through UI (107 s render for a 3.8 s clip) | pass, valid MP4 download |
| Harmful script blocked at API (blocklist + qwen3:8b classifier) | pass, 9/9 on sample set |
| Conversation end writes usage ledger | pass |
| Billing page lists plans; checkout without keys -> clear 501 | pass |

Bugs found and fixed by this pass: empty `{}` responses after commit (conversation end, consent),
default persona LLM pointing at a model that wasn't installed (and the failure being silent),
moderation blocklist too narrow, 1B classifier unusable (switched to qwen3:8b), worker not re-checking moderation.

Not tested: real microphone/speakers (fake mic file only), Stripe/Razorpay live APIs, mobile layout,
concurrency/load, NVIDIA/GPU path (does not exist yet).

## Status (Oct 5)
Backend: `cd backend && .venv/bin/python -m pytest -q` runs 447 tests (all passing). End to end: `make e2e` runs 13 steps against a restarted stack (all green; 24 routes at 1440 and 390 px). Not covered: real microphones and phones, Docker, CUDA/NVIDIA, live payments, and subjective face/voice quality (judged by the owner only; see docs/BENCHMARKS.md).
