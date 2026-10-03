# Mirage: pitch draft (internal; numbers marked TODO must be filled with real data before sending)

## One line
Open-core platform for real-time conversational video agents and consent-based digital replicas: use our API, or self-host the whole stack.

## Problem
Hosted avatar platforms are closed, priced per minute, and hard to embed in products that need data control. Teams that want a talking agent with a face either pay a premium per minute or stitch five open-source models together themselves.

## Product (what runs today, measured)
- Real-time voice agent: speech in, answer back in ~1.4 s to first audio on a laptop (faster-whisper, local LLM, Kokoro TTS).
- Live lip-synced face on Apple Silicon: ~55-130 fps lip-sync render (Wav2Lip on the M1 GPU) streamed into the browser; ~2-2.5 s from transcript to first moving lips (improving).
- Replicas from a short video, gated by spoken, revocable consent; audit log; moderation on scripts.
- Knowledge base + memory, REST API, Python/JS SDKs, embed widget, usage metering, Stripe/Razorpay checkout (not yet live-tested), offline video generation.
- Self-host: voice stack costs $0/min in API fees.

## Why this could win (hypotheses to test, not facts)
1. Cost: self-hosted voice path has no per-minute vendor cost; GPU face is the only variable cost.
2. Trust: consent-first replicas are a procurement requirement for enterprises; make it a feature, not an afterthought.
3. Openness: swap any STT/LLM/TTS/renderer per customer tier; run on-prem.

## Honest gaps
- Face quality is below the best hosted models (96 px mouth model); NVIDIA GPU path is documented but untested.
- No customers, no revenue, no benchmark vs competitors yet (TODO: blind test of 20 people).
- One-person build; production hardening in progress (see docs/SECURITY.md).

## What investors will ask (prepare answers with data)
- TODO: 10 design-partner conversations in one niche (suggest: sales demo avatars or multilingual tutoring in India).
- TODO: cost per user-minute at 1/10/100 concurrent streams on a rented GPU (see docs/UNIT_ECONOMICS.md; assumptions are unmeasured).
- TODO: latency target <1 s and the plan to reach it.
- TODO: retention/conversion from free to paid once there are users.

## Ask
TODO: amount, runway, use of funds (GPU infra, one ML engineer, design partners).
