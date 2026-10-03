# Competitor gaps and what was built (Oct 4 2026)

Complements `docs/FEATURE_PARITY.md` (Oct 3). Competitor facts below come from web searches on Oct 4 2026; each row cites the page the claim rests on. A search summary is not a full read of the vendor docs, so "?" means not confirmed, not "absent".

## Evidence collected

| Vendor | Feature observed | Source |
|---|---|---|
| Tavus | Knowledge base from PDFs, CSVs, PPTX, TXT, images **and URLs / website crawling**; retrieval strategies speed/quality/balanced; recordings stored in the customer's S3 bucket | https://docs.tavus.io/sections/conversational-video-interface/knowledge-base , https://www.tavus.io/lp/knowledge-base |
| HeyGen LiveAvatar | "Context" (knowledge base) accepts URLs, product info, prompts; FULL mode (they run STT/LLM/TTS/turn-taking/memory) vs LITE mode (bring your own agent) | https://help.heygen.com/en/articles/12758516-introducing-liveavatar , https://developers.heygen.com/live-avatar |
| D-ID Agents | RAG knowledge base, conversation-history export for analytics, one-tag embed with data-attribute customisation (position, layout, initial state) | https://docs.d-id.com/docs/embed-overview , https://www.d-id.com/blog/introducing-d-id-agents/ |
| Anam | Sessions recorded by default, kept 30 days, transcript API `GET /v1/sessions/{id}/transcript`; webhook/RAG/client tools; greeting on join | https://anam.ai/changelog , https://docs.anam.ai/tools/introduction |
| Synthesia | Workspace **glossary** applying saved pronunciations automatically; interview avatars that summarise key themes; Interactive Avatars API with BYO knowledge base | https://docs.synthesia.io/docs/pronunciation-controls , https://www.synthesia.io/post/synthesia-new-avatars-dont-just-talk-they-take-action |
| Simli | Speech-to-video under 300 ms, LiveKit and Pipecat integrations (video stage only, so no overlap with our work) | https://docs.simli.com/overview , https://docs.pipecat.ai/api-reference/server/services/video/simli |

## Candidate gaps, ranked

Scored on: buyer-visible, seen at 2+ competitors, no NVIDIA, no paid service, fits in the existing architecture without a rewrite.

| # | Gap | Seen at | Needs GPU/paid? | Decision |
|---|---|---|---|---|
| 1 | Knowledge from URLs + show the sources behind an answer | Tavus, HeyGen, D-ID | no | **built** |
| 2 | Conversation analytics: sentiment, topics, talk ratio | Synthesia (themes), D-ID (export), Tavus | no (lexicon; an LLM pass can replace it) | **built** |
| 3 | Pronunciation glossary for TTS | Synthesia, Tavus (?) | no | **built** |
| 4 | Interruption / turn-taking controls | Tavus (Sparrow tuning, ?), HeyGen FULL mode | no | **built** |
| 5 | Scheduled / appointment call links with calendar file | none confirmed verbatim; adjacent to D-ID/HeyGen AI-SDR use | no | **built** (chosen over recordings, see below) |
| 6 | Call recordings (audio/video) with playback | Tavus, Anam | storage + consent UX; video needs frame capture | not built: touches the realtime audio path and needs a consent decision from the owner |
| 7 | Embed widget customisation | D-ID | no | already exists (`widgets.py`: label, colour, position, greeting, language, domains) |
| 8 | Webhooks for events | all | no | already exists (13 events, signed, retried) |
| 9 | Multi-avatar scenes | Synthesia (video) | GPU for lips | not built |
| 10 | LiveKit/Pipecat adapters, MCP server | Simli, Tavus | no | next candidates |

## What shipped

All tables are new (migration `0003`), all routes under `/v1`, all UI in the persona drawer / conversation detail / analytics page.

1. **URL knowledge + citations.** `POST /personas/{id}/knowledge/url` fetches a page or PDF through the SSRF guard (`netguard`; scheme check always, private-address block in production), 3 MB cap, extracts readable text (scripts, styles, nav, footer dropped, headings kept for the chunker), ingests it. `POST .../knowledge/{doc}/refresh` re-fetches (no-op if the content hash is unchanged, otherwise replaces the chunks). In a live call the excerpts given to the LLM are stored per assistant turn (`TurnCitation`), sent to the client as a `citations` WebSocket event before `agent_done`, and exposed at `GET /conversations/{id}/citations` (title, URL, score, snippet). UI: "Add a web page" under Knowledge, "Sources" disclosure under each agent turn.
2. **Conversation insights.** Local lexicon sentiment with negation, per-user-turn scores, overall label, trend (second half vs first half), topics, talk ratio, questions, interruptions. Stored at conversation end, computed on demand otherwise. `GET /conversations/{id}/insights`, `GET /analytics/insights?days=&persona_id=` (label split, daily average, top topics, worst conversations). UI: Sentiment section in conversation detail and two cards on Analytics.
3. **Pronunciation glossary.** `GET/POST/DELETE /personas/{id}/pronunciations`, `POST .../preview`. Whole-word, longest-term-first, single-pass (no replacement is re-matched), optional case sensitivity. Applied by a TTS wrapper, so the transcript and captions keep the original text. UI: list, add, try-a-sentence preview.
4. **Interruption controls.** `GET/PUT /personas/{id}/voice-tuning`: `interruption_sensitivity` (0-1, scales the number of speech frames needed to barge in: 8 at 0, 5 at 0.5, 2 at 1), `allow_interruptions`, `turn_patience_ms` (300-3000, the end-of-turn silence). Applied to the `Session` when a conversation starts. UI: sliders in the Voice tab.
5. **Scheduled call links.** `PUT/GET/DELETE /share/{token}/schedule` gives a guest link a window (start, duration, invitee, note). Guests before the start get HTTP 425 with `opens_at`; after the end 410; `/guest/{token}/info` reports `opens_at` / `closes_at`; `GET /guest/{token}/schedule.ics` returns an RFC 5545 calendar file. UI: "Schedule a call" in the Share tab.

## Not verified

- No live run with Whisper/Kokoro/Ollama: all realtime behaviour is covered by tests with the fake STT/LLM/TTS used by the existing suite. Whether `interruption_sensitivity` feels right with the real Silero VAD is untested; the mapping is a reasoned guess.
- URL ingestion was tested with an injected fetcher and the guard's refusal paths; no real website was fetched.
- Sentiment is a keyword lexicon (English only); it will miss sarcasm and other languages. Treat it as a triage signal.
- Pronunciation works by text respelling, so quality depends on the TTS engine's reading of the respelling; no audio was listened to.
- Scheduled links do not send emails or reminders (no external service); the invitee fields are stored and shown only.
- Dashboard: type-checked with `NEXT_DIST=.next-gap npx tsc --noEmit`; not rendered in a browser.
