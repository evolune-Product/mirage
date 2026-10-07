# VocalFace

Open-core conversational video AI: talk face to face with an AI agent, make digital-twin replicas, and generate talking-head videos through an API. Built on free and open-source parts (faster-whisper, Ollama, Kokoro, Chatterbox, SoulX-FlashHead) so it can run on one machine.

**Status: working prototype, not a product.** It runs end to end on an Apple-silicon Mac. It has no customers, and some engines are not licensed for commercial use (see below).

## What works today
- **Realtime conversation:** microphone, speech-to-text, LLM, voice, and a lip-synced face over WebSocket. About 1.4 to 1.9 s from the end of speech to first lip movement on an M1 Pro.
- **Replicas:** from your own video (with voice and face consent checks) or from a single photo. Photo avatars get their idle clip from FlashHead (no LivePortrait needed when it is installed).
- **Videos:** script to talking-head video via API. Uses SoulX-FlashHead when installed (whole face and head motion generated from audio); about 19x slower than real time on an M1 Pro.
- **Voice cloning:** Chatterbox (MIT). Measured speaker similarity 0.70 to 0.79 on our model; judged by ear by the owner only.
- **Platform:** personas, knowledge from documents and web pages with citations, objectives and guardrails, tools, webhooks, API keys, workspaces, analytics and sentiment, leads, guest and scheduled links, an embeddable widget.
- **Dashboard and site:** Next.js app in `web/`.
- **Safety:** consent checks, signed URLs, SSRF guard, WebSocket tickets and rate limits, signup bot protection. See `docs/SECURITY.md` for the honest gaps.

## Face model choice
SoulX-FlashHead is the one generative face model we build on. In a one-person blind test it was rated good and Wav2Lip, MuseTalk and JoyVASA all worst (JoyVASA was dropped). Numbers and caveats: `docs/BENCHMARKS.md`.
**Photo tip:** give it a sharp, front-facing photo cropped tight so the face fills roughly half the frame. A loose upper-body photo made the mouth barely move (jaw movement 0.08 versus 0.21 after cropping). The renderer now crops automatically (`workers/face_crop.py`) for both photos and video frames. A tight frame from real video of the person looked best in our owner test.

## Run locally
```
./dev.sh                  # API :8000, lip-sync :8100, worker loop, web :3000
make test                 # backend tests (447 passing on Oct 5)
make e2e                  # full browser end-to-end suite
```
Needs Python 3.11, Node 20, ffmpeg and Ollama (`llama3.2:3b`). Model files are not in git; see `docs/DEPLOY.md`.

## Licences: read before selling anything
Wav2Lip (live lip-sync) and LivePortrait/InsightFace (fallback photo avatars) use non-commercial weights. SoulX-FlashHead says Apache-2.0 in its README, not yet checked by counsel. Details and a commercial-only switch (`VOCALFACE_COMMERCIAL_ONLY=1`): `docs/LICENSES.md`.

## Honest limits
- Real-time FlashHead needs an NVIDIA GPU. Not yet measured: `docs/GPU_RUNBOOK.md`.
- Docker, Postgres, S3, live payments, real microphones on phones, and iOS have not been tested.
- Face realism is below commercial leaders such as Tavus.

Hosting: `netlify.toml` deploys the marketing pages only; the dashboard needs a hosted backend (not set up). GPU test plan: `docs/GPU_RUNBOOK.md`.

More: `docs/FEATURE_PARITY.md`, `docs/COMPETITOR_GAPS.md`, `docs/UNIT_ECONOMICS.md`, `docs/ROADMAP.md`.
