# Roadmap

Done (v0.1): API, tenancy, API keys, credit metering, provider interfaces, Ollama LLM, turn-taking, tests.

Next, in order:
1. Real-time loop: LiveKit self-host + agent worker (faster-whisper -> Ollama -> Kokoro TTS), audio-only first.
2. Avatar renderer worker (LivePortrait/MuseTalk) on GPU, streamed as video track.
3. Replica training worker: video -> face/voice assets, status updates.
4. Video generation batch queue.
5. Web dashboard (Next.js) + playground.
6. Billing (Razorpay/Stripe), moderation and consent verification.
7. Differentiators: multilingual, on-prem, per-minute pricing below Tavus.
