# Unit economics (assumptions, not measurements)

Run `cd backend && .venv/bin/python -m app.billing` to regenerate the table from `app/billing.py`.
Nothing here is benchmarked on a production VocalFace deployment; GPU prices and stream density are **assumptions** to replace with your measured numbers (see BENCHMARKS.md).

## Prices (code: `PLANS`, `TOPUPS`)
| Plan | Price | Included min | Effective $/min | Overage |
|---|---|---|---|---|
| Free | $0 | 10 | 0 | none (hard stop at 0 credits) |
| Starter | $19 | 120 | 0.158 | $0.20/min |
| Pro | $79 | 600 | 0.132 | $0.15/min |
Top-ups: 60 min $12 ($0.20/min), 300 min $50 ($0.167), 1000 min $150 ($0.15).

Overage rates are stored in the plan catalog but **not yet enforced automatically**: credits are a prepaid balance, and users buy top-ups. Plan purchase is one-time per month, not a recurring subscription.

## Cost per user-minute
`cost = GPU $/hr / 60 / (concurrent streams per GPU x utilization) + other` (other applies only to GPU scenarios; the voice-only row assumes 0 and ignores host fixed cost) where utilization is the fraction of paid GPU time with live sessions, and other = $0.005/min for bandwidth, LLM on local Ollama (electricity/amortised), storage.

- Voice-only with local models (Whisper, Ollama, Kokoro/Piper) on the host you already run: about $0 marginal. Only the host's fixed cost remains.
- Rendered face needs a GPU. Assumed rental: $0.40/hr consumer-class, $0.80/hr datacenter-class (L4/A10-ish). Whether a given open model sustains 1, 2, or 3 real-time streams on such a card is an **unverified assumption**.

## Scenarios (gross margin on included-minute price)
| Scenario | Cost/min | Starter | Pro |
|---|---|---|---|
| Local/free voice only (~$0 marginal) | $0.000 | 100% | 100% |
| Consumer GPU, 1 stream, 30% util | $0.027 | 82.8% | 79.3% |
| Consumer GPU, 2 streams, 50% util | $0.012 | 92.6% | 91.1% |
| Datacenter GPU, 3 streams, 50% util | $0.014 | 91.2% | 89.5% |
| Datacenter GPU, 1 stream, 20% util | $0.072 | 54.7% | 45.6% |

(Exact figures: run the module.) Takeaways: margin is dominated by GPU utilization, not list price. Idle GPUs kill margin; free-tier users on rendered video will lose money, so keep Free on voice-only or cap it. Payment fees (Stripe ~2.9% + fixed fee; Razorpay ~2%) come off the top and are not modelled. Not modelled: support, free-tier abuse, refunds, taxes.


## Measured capacity (added by the capacity work, Oct 3 2026; details and raw tables in docs/overnight/capacity.md)
Measured with `backend/loadtest/loadgen.py` on the M1 Pro (34 GB, MPS) **while four other agents kept the GPU at 90-100 %**, so these are
conservative, noisy numbers. Before the fixes the cliff was between 3 and 5 simultaneous conversations (voice N=5: 4/5 ok, first audio 9.6 s;
face N=5: 0/5 ok; face N=8: 0/24 turns). After: voice N=5 5/5 ok (4.6 s median), live face N=5 15/15 turns (6.5 s warm median), N=8 21/24.
Usable (acceptable latency, no drops): about **3 live-face** or **5 voice-only** conversations per Mac process under contention; shipped
admission defaults are 6 total / 3 face (`VOCALFACE_MAX_CONVOS`, `VOCALFACE_MAX_FACE_CONVOS`). First saturated resource: the shared GPU
(TTS on MLX + Wav2Lip + any other GPU job), then Ollama (first token 0.2 s -> 5 s at N=5-8 with default parallelism); the API event loop
stayed at 20-55 % of one core.

### Estimate for a rented NVIDIA box (NOT measured; assumptions explicit)
Assumptions: one 24 GB card (L4/A10-class, $0.80/h) + 8 vCPU; Wav2Lip at >= 500 fps (about 20x real time, vs ~150 fps measured on MPS, a
speaking stream needs 25 fps and the agent speaks ~40 % of the time), Kokoro and llama3.2:1b/3b on the same card, 10 live-face streams
(optimistic) or 4 (pessimistic, if CPU VAD/STT/encoding binds first), plus the $0.005/min other costs used above.
| Streams per box | Utilisation | Cost / user-minute |
|---|---|---|
| 10 | 20 % | $0.012 |
| 10 | 50 % | $0.008 |
| 10 | 80 % | $0.007 |
| 4 | 20 % | $0.022 |
| 4 | 50 % | $0.012 |
Takeaway unchanged: utilisation matters more than per-stream speed; at 20 % utilisation and 4 streams the Starter plan margin is ~86 %
($0.158/min), at 50 % and 10 streams ~95 %.

## FlashHead cost note (Oct 5, unmeasured)
Generated-video cost on the Mac is about 25-30 s of compute per video second (Lite) and about 250 s (Pro), so the Mac is a development box only. The FlashHead authors report Lite at 96 fps or 3 concurrent real-time streams on one RTX 4090; we have NOT measured this. If true, a rented 4090 at roughly $0.3-1/h gives about $0.1-0.3 per user-minute for live video before any optimisation (my estimate). Replace this paragraph with measured numbers after the GPU test (docs/GPU_RUNBOOK.md).
