# Roadmap

**Status Oct 5:** done: API, tenancy, billing hooks, real-time voice loop, live lip-sync (Wav2Lip, dev only), replicas from video and photo, FlashHead video renderer with automatic tight crop, voice cloning, dashboard and site, consent and security hardening, competitor-gap features (docs/COMPETITOR_GAPS.md), 447 backend tests and a 13-step end-to-end suite.

**Next, in order:**
1. Rent one NVIDIA GPU for an evening: measure real-time FlashHead (docs/GPU_RUNBOOK.md); decide on a streaming adapter for the live path.
2. Replace the non-commercial live engine (Wav2Lip) and photo fallback (LivePortrait/InsightFace); lawyer check of FlashHead, MuseTalk and the eSpeak GPL question.
3. Pick one niche, win 5-10 pilot users, and collect consented face/voice data (the real moat).
4. Hosted backend (Docker/Postgres untested) so the dashboard can be public.
5. Optional: fine-tuning or a model of our own on a funded GPU budget (our from-scratch and LoRA trials were negative; docs/LORA_TRIAL.md).

**Tried and dropped:** JoyVASA, Ditto, EchoMimic V3, Wan2.2 (small), MuseTalk (slow), a from-scratch model (too little data), a personal LoRA.

---
Original v0.1 plan (kept for history):

Done (v0.1): API, tenancy, API keys, credit metering, provider interfaces, Ollama LLM, turn-taking, tests.

Next, in order:
1. Real-time loop: LiveKit self-host + agent worker (faster-whisper -> Ollama -> Kokoro TTS), audio-only first.
2. Avatar renderer worker (LivePortrait/MuseTalk) on GPU, streamed as video track.
3. Replica training worker: video -> face/voice assets, status updates.
4. Video generation batch queue.
5. Web dashboard (Next.js) + playground.
6. Billing (Razorpay/Stripe), moderation and consent verification.
7. Differentiators: multilingual, on-prem, per-minute pricing below Tavus.
