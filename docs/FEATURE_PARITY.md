# Feature parity: Tavus, HeyGen, Synthesia, D-ID, Simli, Hedra vs Mirage

Written Oct 3 2026. Competitor columns come from their public docs / marketing pages as fetched that night (Tavus `docs.tavus.io/llms.txt` index, HeyGen `developers.heygen.com`, Synthesia features page, D-ID `docs.d-id.com`, simli.com, hedra.com). Anything marked "?" was not confirmed by a page I could read, it is not a claim that the feature does not exist. Mirage status is `have` (built and verified), `partial` (built, with a stated gap), `missing`.

Legend for Mirage "verified": **unit** = automated tests with fakes, **live** = exercised with the real local stack (Whisper, Kokoro, Ollama qwen3:8b, a real Chromium with fake mic) on Oct 3 2026.

## Real-time conversation (CVI)

| Feature | Tavus | HeyGen | Synthesia | D-ID | Simli | Hedra | Mirage | Notes |
|---|---|---|---|---|---|---|---|---|
| Real-time face-to-face agent | CVI/PALs (Phoenix face, <1 s) | interactive avatar (?) | Interactive Avatars API | Agents | <300 ms speech-to-video (video stage only) | real-time avatars | partial | Works end to end with Wav2Lip lips; 2.0-2.5 s to first moving lips; mouth quality blurry at close range (see ROADMAP) |
| Voice-only conversation | yes (audio-only) | ? | ? | ? | n/a | ? | have (live) | The default path; ~1.4 s to first audio with the 1B model |
| Turn-taking / barge-in | Sparrow | yes | ? | ? | n/a | ? | have (unit) | energy VAD + interrupt; semantic turn model is missing |
| Knowledge base (docs, RAG) | yes | ? | ? | yes (agent knowledge) | n/a | ? | have (unit, live) | PDF/text, fastembed + BM25 fallback |
| Persona objectives | yes | no | roleplay/survey sessions | ? | n/a | ? | have (unit, live) | LLM-judged completion + extracted variables, WS event + webhook |
| Guardrails | yes | no | ? | ? | n/a | ? | have (unit) | prompt rules + clause-level output filter with fallback line + webhook |
| Memories across conversations | yes | no | ? | ? | n/a | ? | have (unit, live summary) | LLM summary at end -> memory, scoped by `participant_id` |
| Tool / function calling | yes (LLM tools, MCP, perception tools) | no | ? | ? | n/a | MCP server | have (unit, live) | Ollama + OpenAI-compatible tools, signed webhook, result spoken. No MCP |
| Custom LLM | yes | no | ? | ? | bring your own | ? | have (unit) | any OpenAI-compatible endpoint, key stored encrypted. Not run against a hosted vendor |
| Greeting (agent speaks first) | custom greeting | n/a | ? | ? | n/a | ? | have (unit, live) | with `{{variables}}` |
| Conversation variables / context | conversational context | n/a | ? | ? | n/a | n/a | have (unit, live) | `variables`, `context`, `participant_id` on create |
| Multilingual STT/TTS | 30+ languages | 30+ (video) | 140+ (video voices) | yes | ? | ? | partial (live es, hi) | 9 TTS languages via Kokoro, STT via Whisper (more); no voice cloning, quality varies |
| Max call duration / timeouts | yes | n/a | ? | ? | n/a | n/a | have (unit) | per-conversation `max_seconds` + credit exhaustion close the stream (4408) |
| Transcripts per conversation | yes | n/a | ? | ? | n/a | n/a | have (unit, live) | persisted per turn with latency + interruption flags |
| Conversation summary | via post-call | n/a | ? | ? | n/a | n/a | have (live) | |
| Conversation recordings (audio/video) | yes | n/a | ? | ? | n/a | n/a | missing | transcripts only; recordings need storage + consent UX |
| Closed captions | yes | ? | ? | ? | n/a | ? | partial | the playground logs transcript lines; no styled captions |
| Perception (camera understanding), screen share | Raven, yes | no | no | no | no | no | missing | big differentiator for Tavus; needs a vision model (e.g. Qwen2-VL) |
| Magic Canvas / on-screen components | yes | no | interactive elements (video) | no | no | canvas (agent) | missing | |
| Pronunciation dictionaries | yes | ? | ? | ? | n/a | ? | missing | |
| Emotion control on face | yes | ? | expressive | v4 expressive | ? | ? | missing | |
| Webhooks | yes | yes | yes | yes | ? | ? | have (unit, live) | HMAC-signed, retries, delivery log, 11 events |
| Shareable link / embed | landing page, widget, iframe | share link | public page / embed | embed | n/a | n/a | have (unit, live) | `/guest/{token}` page with seconds cap, total cost cap, rate limits, expiry, revoke; `sdk/embed/widget.js` exists |
| Mobile / Zoom / Meet / Teams | yes | no | no | no | no | no | missing | |
| LiveKit / Pipecat integration | yes | no | no | no | LiveKit/Pipecat plugins | ? | missing | the session core is transport-agnostic, adapters not written |
| Private rooms, participant limits | yes | n/a | n/a | n/a | n/a | n/a | missing | one agent + one browser per conversation |

## Replicas, avatars, video generation

| Feature | Tavus | HeyGen | Synthesia | D-ID | Hedra | Mirage | Notes |
|---|---|---|---|---|---|---|---|
| Replica from a training video | yes | yes | personal avatars | instant avatars | ? | have (live) | consent gate; LivePortrait / Wav2Lip |
| Replica from an image | yes | yes | yes | yes (photo) | yes | missing | |
| Consent verification for likeness | yes | ? | consent verification | ? | ? | have (unit, live) | challenge phrase + audio check (safety module) |
| Voice cloning | yes | yes | yes | yes | ? | missing | preset Kokoro voices only |
| Text-to-video via API | yes | yes | yes | yes | yes (jobs API) | have (live) | ~107 s render for a 3.8 s clip on M1; quality limited |
| Template variables in scripts | yes | templates (?) | templates | ? | ? | have (unit) | `{{first_name}}`, preview endpoint |
| Bulk generation from rows | yes (API loops) | yes (?) | yes | ? | ? | have (unit) | `/v1/video-jobs/bulk`, batch status, `video_batch.completed` |
| Translation / dubbing | ? | 30+ languages with lip-sync | 1-click translate + dubbing | video translate | ? | partial (unit) | LLM script translation + per-language voice and re-render; no source-audio dubbing, no voice preservation |
| Video webhooks | yes | yes | yes | yes | ? | have (unit) | `video.ready`, `video.error` |
| Backgrounds / brand kits | yes | yes | brand kits | ? | ? | missing | |
| Video analytics (views, engagement) | ? | ? | yes | ? | ? | missing | |
| SCORM / LMS export | no | no | yes | no | no | missing | |
| Interactive video elements (CTAs, quizzes, branching) | no | no | yes | no | no | missing | |

## Platform / enterprise

| Feature | Tavus | HeyGen | Synthesia | D-ID | Mirage | Notes |
|---|---|---|---|---|---|---|
| API keys, multiple, revocable | yes | yes | yes | yes | have (unit) | hashed storage, per-key last-used, legacy key kept |
| Usage / analytics API | yes | yes | yes | yes | have (unit) | conversations + minutes per day, first-audio latency, top personas, video counts |
| Billing (credits/plans) | yes | yes | yes | yes | have | Stripe + Razorpay wiring, 501 until keys exist |
| Teams, roles, SSO | yes | yes | yes | yes | missing | single owner per account |
| SOC 2 / compliance programme | EU AI Act page | ? | SOC 2 | ? | missing | see docs/SECURITY.md for what exists |
| Audit log | ? | ? | ? | ? | have | safety module |
| MCP server / CLI for agents | yes | ? | ? | ? | missing | |
| Python / JS SDK | yes | yes | yes | yes | have (unit) | updated for every endpoint above |

## What this overnight pass built (backend-features)
1. Transcripts persisted per turn, summary + persona memory wired into later conversations (scoped by participant).
2. Webhooks (HMAC-SHA256, DB queue, backoff, delivery log, 11 events).
3. Persona config: language, greeting, objectives, guardrails, custom LLM, tools.
4. Multilingual (Whisper multilingual + Kokoro voices per language, `GET /v1/voices`).
5. API key management. 6. Analytics endpoint. 7. Video variables, bulk, translation variants, video webhooks.
8. Guest share links with page, caps and rate limits.

## Prioritised plan (what to build next, in order)
1. **Latency to Tavus level** (voice-latency work): the single most visible gap. First audio 1.4 s voice-only / 2-2.5 s with lips vs <1 s. Without it everything else reads as a demo.
2. **Perception** (camera frames to a small VLM, a `perception` tool event): Tavus's headline differentiator and the only listed feature no other competitor has. Start with one frame every 2 s into Qwen2-VL / Moondream, expose results as context + a tool-call style event.
3. **Recordings** (audio first, mp4 later) with per-conversation consent prompt and signed download links; needed for enterprise and for QA of agents.
4. **Voice cloning** (OpenVoice v2 tone conversion on top of Kokoro, already sketched in `jobs.VoiceProvider`) so translated videos and agents keep the person's voice. Required to be credible against HeyGen/Synthesia translation.
5. **LiveKit/Pipecat adapters + Zoom/Meet bots**: distribution. The Session is transport-agnostic, so this is mostly glue.
6. **Image-to-replica** and a better face model on GPU (MuseTalk / LatentSync on a rented 4090) for lip quality.
7. **Teams/roles/SSO + SOC 2 groundwork**, MCP server so agents (Claude, Cursor) can build PALs the way Tavus allows.
8. Interactive video elements / SCORM / analytics for the async-video side (Synthesia territory); only if the video product becomes the focus.
9. Magic-Canvas-style on-screen components (cards, forms, scheduling) driven by tool results.

Not worth chasing early: Simli-style sub-300 ms video stage (we lose more time in STT/LLM/TTS), emotion control, pronunciation dictionaries.
