# Third-party notices

VocalFace is built on open-source work. This file credits it. Licence terms are summarised from each project's own repository on 2026-10-03; the full audit with evidence and open questions is `docs/LICENSES.md`. This is not legal advice: have counsel review it before commercial launch.

When you ship or redistribute any of these, keep their LICENSE and NOTICE files. The copies we rely on are in `workers/` (for example `workers/SoulX-FlashHead/LICENSE`).

## Used in the commercial path

| Project | Used for | Licence (per its repository) |
|---|---|---|
| SoulX-FlashHead (Soul AI Lab) | face and head motion from audio | Apache-2.0 (code); model weights as stated on its model card, not yet reviewed by counsel |
| Chatterbox (Resemble AI) | voice cloning | MIT |
| Kokoro | text to speech | Apache-2.0 (note: its eSpeak NG phonemiser is GPL-3.0, see `docs/LICENSES.md`) |
| faster-whisper / Whisper (OpenAI) | speech to text | MIT |
| Silero VAD | voice activity detection | MIT |
| Ollama and the language models you pull | answers | Ollama MIT; each model has its own terms |
| MediaPipe (Google) | face tracking and tight face crop | Apache-2.0 |
| OpenCV | image processing | Apache-2.0 |

## Development and demo only (non-commercial, do not ship to customers)

| Project | Why it is restricted |
|---|---|
| Wav2Lip | weights are research / non-commercial only |
| LivePortrait with InsightFace models | InsightFace models are non-commercial research only |

`VOCALFACE_COMMERCIAL_ONLY=1` disables these. Replacing both is on the roadmap (`docs/ROADMAP.md`).

## Trademarks

NVIDIA and NVIDIA Inception are trademarks of NVIDIA Corporation. Membership in the program does not imply endorsement. All other names belong to their owners and are used only to credit their work.
