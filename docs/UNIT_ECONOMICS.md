# Unit economics (assumptions, not measurements)

Run `cd backend && .venv/bin/python -m app.billing` to regenerate the table from `app/billing.py`.
Nothing here is benchmarked on a production Mirage deployment; GPU prices and stream density are **assumptions** to replace with your measured numbers (see BENCHMARKS.md).

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
