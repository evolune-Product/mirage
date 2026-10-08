# Voice cloning (overnight, agent "clone")

Status: DONE for Chatterbox (MIT) on MLX; end-to-end verified through the real API with the owner's voice. Results and honest limits below.

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
* Downloads from Hugging Face ran at ~0.1-0.5 MB/s tonight (shared network); the default `hf` xet downloader stalled at 0 bytes, so the Chatterbox 4-bit weights (607 MB) were fetched with parallel `curl -r` ranges into `models/chatterbox-4bit` (git-ignored) and loaded from that local path (`VOCALFACE_CLONE_REPO=<dir>`). On a normal connection `mlx-community/chatterbox-4bit` downloads automatically on first start.
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
* Consent-verification threshold is 0.50; the clone acceptance threshold used here is 0.30 (`VOCALFACE_CLONE_MIN_SIMILARITY`).
* Reference picker on the founder video: window 38.4-57.5 s, 19.6 s, SNR 23 dB with the gentle denoise (21 dB without), 0 clipped samples, Whisper reads it as one coherent monologue.

## Results (M1 Pro, shared and loaded: load average 4-9, so timings are pessimistic and noisy; Chatterbox 4-bit multilingual MLX)

Offline eval (`backend/scripts_voice_clone_eval.py`, reference = 19.6 s picked from the founder video, WeSpeaker similarity / Whisper WER; warm runs):

| language | sentence | audio s | synth s (= first audio, not incremental) | RTF | similarity | WER |
|---|---|---|---|---|---|---|
| en | 5 words | 1.5-1.9 | 3.5-3.9 | 2.1-2.3 | n/a (<1.5 s speech) | 0.0 |
| en | 13 words | 3.5-4.5 | 3.6-3.8 | 0.84-1.04 | 0.66-0.78 | 0.36-0.57 (Whisper writes "$19" for "nineteen dollars"; text was right) |
| en | 2 sentences | 5.1-5.2 | 6.7-7.2 | 1.3-1.4 | 0.61-0.73 | 0.0-0.05 |
| es | 15 words | 6.1-6.3 | 6.2-8.4 | 1.0-1.4 | 0.68-0.70 | 0.06 |
| hi | 13 words | 6.0 | 6.4-7.2 | 1.1-1.2 | 0.735 | INCONCLUSIVE (Whisper small garbles Devanagari; WER 0.23 and 0.77 after fixing the tokenizer; I cannot judge Hindi by ear) |

Floor / ceiling: Kokoro presets score -0.17..0.03 against the owner, the same person in other segments of the video 0.91. Clones land at 0.6-0.78: clearly the same-speaker side of the consent threshold (0.50) but well below a real recording (voice similar, not identical). Startup: sidecar 2.4-4.7 s (weights cached), speaker conditioning 0.35-1.8 s once per reference.

End-to-end through the REAL API (isolated server :8280, owner's founder video as the replica):
* Worker made the clone automatically after the replica became ready: reference 19.6 s, SNR 22.6 dB, similarity 0.703, WER 0.048, test RTF 4.3 (cold + loaded).
* Live conversation (WebSocket, llama3.2:1b, Kokoro-synthesised question): persona `tts_voice=clone:<id>` replied with the correct text; audio similarity to the owner 0.768 (Kokoro default voice on the same turn: -0.037). First agent audio after the end of speech: Kokoro 1.85 and 2.49 s, clone 7.72 and 6.03 s (+4-5 s).
* Offline video (`voice: "clone"`, worker): audio extracted from the rendered mp4 has similarity 0.783 and WER 0.0 for the 2-sentence script (the video job took about 7 min total on the loaded machine, mostly rendering).
* Consent gate verified live: no clone while the replica was not ready/consented; after `DELETE /consent` the `voice_clone/` directory was gone, status `revoked`, `usable_in_conversations=false`, `POST /voice` -> 403, voice absent from `GET /v1/voices`. Audit rows present (requested, ready, used x2, refused, ...).
* Consent in this E2E was the dev TYPED consent (I do not have the owner saying the challenge phrase); the production path (`asr-phrase+voice-match` only) is covered by unit tests, not by a human recording.

## Latency budget and recommendation
* Chatterbox is not incremental inside a sentence and runs at RTF about 1.0-1.4 for sentences (up to 2+ for very short ones; 4+ cold), so first audio is the whole first chunk: about 2.2-3.5 s for 3-7 words vs 0.13-0.3 s Kokoro MLX. Measured end to end: +4-5 s first audio on a loaded machine. RTF is not < 1, so audio would also stall between sentences.
* Recommendation: use the clone for VIDEO GENERATION (offline, quality over latency; works, similarity about 0.78) and for the preview endpoint; keep Kokoro for LIVE conversations. Live cloned voice works functionally (`clone:<id>` persona voice, fallback, consent) and is acceptable only for non-real-time uses (e.g. voicemail-style replies) or on a GPU box (an NVIDIA GPU should bring RTF well below 1; not tested).
* Possible live paths NOT tried: Pocket TTS (Kyutai, 100M, claims faster than real time on CPU with cloning; weights CC-BY-4.0; not downloaded because Hugging Face ran at 0.1-0.5 MB/s and Kyutai's own repo contains a `remove_voice_cloning_and_push.py` script, so the public checkpoint may have cloning removed), OpenVoice v2 tone-colour conversion on top of Kokoro (MIT; converts Kokoro audio so first audio would be Kokoro latency + conversion; not installed), Qwen3-TTS 0.6B Base (Apache-2.0, 10 languages, no Hindi; 2.3 GB, not downloaded).

## Multilingual
Chatterbox 4-bit MLX is the multilingual checkpoint (23 languages). Verified by synthesis + Whisper: English, Spanish (WER 0.06, similarity 0.68-0.70). Hindi synthesises and the speaker similarity holds (0.735) but I could not verify intelligibility (see table); the `clone:<id>@es` / persona-language wiring routes the language code to the model. Qwen3-TTS and Pocket TTS were not evaluated; Qwen3 would lack Hindi.

## Quality limits (honest)
* I cannot listen. All quality claims are objective proxies (WeSpeaker similarity, Whisper round trip). Similarity 0.6-0.78 means recognisably the same speaker by the model, not necessarily to a human ear; a human A/B is still needed. Prosody/emotion control was left at `exaggeration=0.3`.
* Similarity varied 0.61-0.78 between runs of the same sentence (sampling randomness); short clips (<1.5 s) cannot be scored.
* The reference carries the room acoustics and Zoom-style compression of the founder video; a better microphone clip would help.
* The status API `similarity` is for one fixed English test sentence.

## What is unfinished / not verified
* Live conversation clone latency is too high (see above); no streaming inside a sentence.
* Pocket TTS, OpenVoice v2 and Qwen3-TTS were NOT evaluated (network speed), only Chatterbox.
* Hindi intelligibility; fr/de/other languages; the fp16 checkpoint (2.6 GB) was not compared with 4-bit.
* Production consent path (`asr-phrase+voice-match`) not exercised with a real human recording; dashboard UI for voices not built (API + SDK only).
* The sidecar crashed once with "sidecar exited" during the multi-language eval run (Hindi step) and did not repeat; `VOCALFACE_CLONE_LOG=<file>` captures its stderr if it recurs. Circuit breaker and Kokoro fallback cover it.
* `.venv-clone` and `models/chatterbox-4bit` are git-ignored; `backend/scripts_setup_clone.sh` documents the setup. HF weights downloaded by hand via curl ranges because the xet downloader stalled; `mlx-audio` additionally fetches `mlx-community/S3TokenizerV2` (495 MB) into the HF cache.

## Licences (final)
Chatterbox MIT (weights MIT upstream; the mlx-community card lists Apache-2.0 for the conversion) and S3TokenizerV2 via mlx-audio (MIT) -> commercial use OK. Adopted default: Chatterbox. Rejected as defaults: Coqui XTTS (CPML), F5-TTS weights (CC-BY-NC), Fish-Speech weights (CC-BY-NC-SA). Reminder: Chatterbox embeds a Resemble "PerTh" watermark in generated audio upstream; the MLX port was not checked for it.

## Oct 4 audit (clone samples)
* demo_assets/demo_face_v2.mp4 audio is Kokoro af_heart (0.847 vs af_heart), not a human: do not use it as a "real voice" reference. Use the founder video.
* Added `app/voice_clone/audio_checks.py` (clipping, silence, clicks, NaN, bandwidth) with a test, and `scripts_voice_clone_audit.py` (similarity vs reference and Kokoro, WER, checks). Results: ~/Desktop/VocalFace_clone_samples/README.md.
