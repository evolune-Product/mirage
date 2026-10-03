# Voice cloning (overnight, agent "clone")

Status: IN PROGRESS (notes written incrementally; final report at the bottom when done).

## Candidates and licences (checked via web search + model cards, Oct 3 2026)

| Model | Code licence | Weights licence | Commercial use | Languages | Runs on M1 |
|---|---|---|---|---|---|
| Chatterbox (Resemble AI) | MIT | MIT | yes | en (original), 23 langs in the multilingual checkpoint | MLX port in mlx-audio (`mlx-community/chatterbox-fp16/-4bit/-6bit`) |
| Qwen3-TTS 0.6B/1.7B Base (Alibaba) | Apache-2.0 | Apache-2.0 | yes | zh en ja ko de fr ru pt es it (NO Hindi, community LoRA only) | MLX port in mlx-audio |
| Kyutai Pocket TTS (100M) | MIT | CC-BY-4.0 | yes, with attribution | en | MLX/CPU |
| OpenVoice v2 (MyShell) tone-colour converter | MIT | MIT | yes | any source audio (convert Kokoro output) | torch, MPS/CPU; not evaluated unless noted below |
| Coqui XTTS-v2 | CPML | CPML | NO (non-commercial) | - | rejected |
| F5-TTS | MIT | CC-BY-NC | NO | - | rejected |
| Fish-Speech/OpenAudio | Apache | CC-BY-NC-SA | NO | - | rejected |

Everything runs through `mlx-audio` 0.5.7 in its own venv `.venv-clone` (python 3.11, created like `.venv-tts`; not committed, see `backend/scripts_setup_clone.sh`).

## Environment notes
* Downloads from Hugging Face ran at ~0.1-0.5 MB/s tonight (shared network); the default `hf` xet downloader stalled at 0 bytes, so the Chatterbox 4-bit weights (607 MB) were fetched with parallel `curl -r` ranges into `models/chatterbox-4bit` (git-ignored) and loaded from that local path (`MIRAGE_CLONE_REPO=<dir>`). On a normal connection `mlx-community/chatterbox-4bit` downloads automatically on first start.
* The `mlx-community/chatterbox-4bit` checkpoint is the MULTILINGUAL Chatterbox (config `multilingual: true`, 23 languages incl. es and hi); the fp16 repo (2.6 GB) was not downloaded (bandwidth).

## Design (what was built)
* `backend/app/voice_clone/` (new package): `refprep.py` (decode + gentle denoise + silence trim + best 15-20 s contiguous clean run), `clone_worker.py` (sidecar process in `.venv-clone`, Chatterbox via mlx-audio, caches speaker conditioning per reference), `sidecar.py` (thread-based client: async streaming + blocking), `service.py` (consent gate, lifecycle, quality test, deletion), `metrics.py` (WER).
* `models_voice.py`: table `replicavoice` (new; keyed by `replica_id`, so `data_deletion` removes rows with the replica/account automatically). Audio lives in `<replica_dir>/voice_clone/` and is removed with the replica dir.
* `routers/voice_clone_api.py`: `POST/GET/DELETE /v1/replicas/{id}/voice`, `POST /v1/replicas/{id}/voice/preview`; `voices_api.py` lists cloned voices in `GET /v1/voices`.
* `pipeline/providers_clone.py`: `CloneRoutingTTS` (live; wraps the Kokoro TTS singleton, voice id `clone:<replica_id>[@lang]`, circuit breaker, Kokoro fallback, no double speaking after partial audio), `CloneAwareVoice` (offline video voice provider, same fallback), `resolve_persona_voice`.
* Minimal hooks in shared files: `session.py` (`_with_clone` wrap + `warmup_providers(..., voice)`), `realtime.py` (resolve the persona voice, pass it to warm-up), `jobs.py` (default `Deps.voice`, worker hook after a replica turns ready), `languages.py` (`LanguageTTS` passes the language to a clone voice), `routers/resources.py` (persona `tts_voice` validation), `routers/consent.py` (revoke deletes the voice), `.gitignore`.
* Consent gate (checked at request time, in the worker, and again at EVERY synthesis call): non-revoked `ConsentRecord` with `verified_by == "asr-phrase+voice-match"` (typed dev consent accepted only outside production); owner account only; revoke => files deleted at once (endpoint hook) and, as defence in depth, lazily by the synthesis-time gate; audit rows `voice.clone_requested|ready|failed|refused|used|fallback|deleted|revoked`.

## Baselines measured (WeSpeaker cosine vs the owner's 19.6 s reference picked from the founder video)
* Same person, other segments of the same video: 0.911 and 0.913 (the ceiling a perfect clone could reach).
* Kokoro preset voices saying a 2-sentence reply: af_heart -0.036, am_michael -0.034, am_adam -0.063, bm_george -0.171, af_nova 0.025 (the floor: a non-cloned voice scores about 0).
* Consent-verification threshold is 0.50; the clone acceptance threshold used here is 0.30 (`MIRAGE_CLONE_MIN_SIMILARITY`).
* Reference picker on the founder video: window 38.4-57.5 s, 19.6 s, SNR 23 dB with the gentle denoise (21 dB without), 0 clipped samples, Whisper reads it as one coherent monologue.
