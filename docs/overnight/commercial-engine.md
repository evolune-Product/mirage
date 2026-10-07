# Commercial-safe lip-sync engine (agent "lic", Oct 3 2026, M1 Pro 34 GB, shared + noisy)

Status log (updated as work lands). Nothing committed. Shared servers 8000/8100/3000 untouched; own stack api 8400, lipsync 8401, video 8802.

## Done
* `docs/LICENSES.md`: audit of every model/weight/library with evidence. Headline findings:
  * Wav2Lip: non-commercial (LRS2), confirmed on disk + upstream. LivePortrait: code MIT, **InsightFace buffalo_l weights non-commercial** (stated in LivePortrait's own LICENSE).
  * MuseTalk code MIT; unet "any purpose, even commercially" (vendor) but trained on HDTF + an undisclosed private set -> marked UNCLEAR (needs a lawyer). VocalFace loads only unet + sd-vae-ft-mse (MIT) + whisper-tiny (MIT); DWPose / face-parse-bisent (CelebAMask-HQ) / S3FD are NOT loaded.
  * **New finding outside lip-sync: the Kokoro TTS path pulls eSpeak NG + `phonemizer-fork` (GPL-3.0+).** Fine for pure hosting, a problem for distributed images. Not fixed by the commercial flag.
* `workers/engines/` package: `base.py` (LipsyncEngine interface, `LicenceInfo`/`Weight`), `wav2lip.py` (research-only), `musetalk.py` (adapter, `VOCALFACE_MUSETALK_RES`), `viseme.py` (licence-clean fallback), registry + gate in `__init__.py`.
* `lipsync_server.py`: engine chosen by `VOCALFACE_LIPSYNC_ENGINE=auto|viseme|musetalk|wav2lip`; `/health` reports engine, licence, `commercial_only`, `disabled_engines`, per-engine status; `VOCALFACE_COMMERCIAL_ONLY=1` refuses research engines (503 with the reason on `/render`), `VOCALFACE_COMMERCIAL_ALLOW_UNCLEAR=1` is the explicit opt-in for MuseTalk. `render_offline.py` (+`--engine viseme`), `photo_idle.py`, `render_liveportrait.py` refuse under the flag.
* Tests: `backend/tests/test_engines.py` (9) driving `workers/engine_checks.py`.

## Verified / measured (details appended below as they finish)

### Viseme engine (licence-clean fallback), founder footage 576x324 crop, Kokoro 10.8 s clip
* Speed: 227-247 fps end to end on CPU (incl. audio features), ~3 ms/frame warp+interior; composite+JPEG 2-3 ms/frame. Wav2Lip on MPS measured 61 fps in the same run (cold). Real-time needs 25.
* Looks (6-frame side-by-side vs Wav2Lip, 4x mouth crops viewed): single clean mouth, crisp (it is the real frame), teeth band + dark cavity + tongue painted procedurally. Clearly a puppet: loud vowels open a bit wider than Wav2Lip, tongue/teeth are generic, no phoneme-specific shapes (f/s/th look alike). First gain (0.5) looked like yawning; default now 0.27 (`VOCALFACE_VISEME_GAIN`).
* Objective (weak, favours an energy-driven method by construction): correlation of mediapipe jaw-open vs audio envelope: viseme 0.76, Wav2Lip 0.15 (0.30 at 2-frame lag). No SyncNet/LSE metric was computed.
* Browser (isolated stack 8400/8401/8802, `VOCALFACE_COMMERCIAL_ONLY=1`, fake mic, Playwright): /health showed engine=viseme, disabled=[musetalk, wav2lip]; conversation answered from the document, 7 segments, 73 speaking frames, canvas pixels changed, no page errors. Latency transcript->first lip frame was 10.1 s only because the Mac was swapping (31 GB used); not representative.

### MuseTalk 1.5 on this Mac (MPS fp16, bs 8, 100 frames, UNet+VAE decode only; base VAE encode ~10 s one-time per replica)
| input size | fps (clean runs) | look |
|---|---|---|
| 256 (trained) | 3.6 (0.6 twice under heavy memory pressure) | most natural, softer than source |
| 192 | 3.7 | acceptable |
| 128 | 6.8 | smeared lips/moustache, wrong shapes |
MuseTalk is single-step (t=0), so "fewer steps" does not exist; shrinking resolution gains little on MPS (not conv-bound) and costs quality. Not live-capable here; needs CUDA (vendor claims 30+ fps on V100, NOT verified).

## Recommendation
* (a) Live on Apple Silicon: ship **viseme** under VOCALFACE_COMMERCIAL_ONLY (only option that is both licence-clean and >25 fps). Wav2Lip is faster/prettier but unshippable. Be upfront that it is a puppet.
* (b) Offline video: **MuseTalk 256** after a lawyer clears the unet (HDTF+private data), enabled with VOCALFACE_COMMERCIAL_ALLOW_UNCLEAR=1; fall back to viseme otherwise.
* (c) NVIDIA: **MuseTalk** (live-capable there per vendor; verify on a rented GPU), viseme as fallback.

## Plan: owner-licensed lip-sync model (not started)
* Architecture: MuseTalk code is MIT with released training code; train from scratch/fine-tune on clean data. Do NOT reuse Wav2Lip code (also non-commercial). Audio encoder whisper-tiny (MIT).
* Data (the real blocker; LRS2/LRS3/VoxCeleb/MEAD/CelebV-HQ are research-only; HDTF is CC BY 4.0 but YouTube-sourced): commission >=100 h of 1080p 25-30 fps frontal talking video, >=500 consented speakers with signed model releases covering ML training, multilingual, varied lighting/glasses/beards; plus ~10 h held-out for eval. Estimated $20-60 per recorded hour via talent platforms -> $3-8k, or customer-contributed footage under ToS.
* Effort: legal+consent pipeline 1-2 wk, collection 4-8 wk (parallel), preprocessing 1-2 wk, train+iterate 3-4 wk (8xA100, ~$12-16/h, 2-3k$ per full run, 3 runs), own SyncNet for LSE eval 1 wk, productionisation 2 wk. Total ~3 months, ~$15-25k.
* Cheaper interim: per-customer fine-tune on the customer's own consented footage on top of a cleared base.

## Not verified
CUDA anything; MuseTalk 30 fps claim; real mic/speakers; SyncNet-style sync metric; visual quality on other faces/footage (only the founder clip); legal conclusions (not legal advice); Real-ESRGAN/GFPGAN/CelebAMask-HQ licence texts not read.
