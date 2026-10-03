# Creative features (agent: creative, Oct 3 2026, M1 Pro 34 GB)

Scope: photo avatar, custom backgrounds, captions + formats + watermark + thumbnails, multi-scene videos, render speed.
Nothing committed or pushed. Shared servers (8000/8100/3000) untouched; own isolated stack api 8270, lipsync 8271, video 8772.

## Progress log (written incrementally)
* [done] Photo -> idle clip with LivePortrait (`workers/photo_idle.py`). Synthetic calm template: neutral face + slow head sway +
  one replayed real blink + the 6 lip keypoints frozen (zero relative lip motion). First tries: the photo I picked had an open mouth
  (frame in mid-word), the mouth stayed exactly as in the photo (jaw-open 0.37 constant), so the photo must have a closed mouth;
  with a closed-mouth photo the generated clip has jaw-open mean 0.001 (max 0.003), blink score up to 0.77, head moves ~5 px.
  Cost: 2.3 s per LivePortrait frame on MPS (50 frames for a 4 s / 25 fps loop = ~115 s, then ffmpeg blends to 25 fps).
* [done] `workers/background.py` (MediaPipe selfie segmentation + replace), applied once inside `face_render.prepare_base`
  (cached with the base, so live fps is unchanged). 67 ms/frame while LivePortrait was running in parallel.
* [verified offline] workers/creative_render.py (Wav2Lip + background + captions bold/classic/karaoke/minimal + 16:9/9:16/1:1 + logo + 2-scene
  fade transition + thumbnail + srt) on the photo-generated idle clip. 12.9 s two-scene 720p video rendered in 20.8 s total (base prep with
  segmentation ~4.4 s per background, Wav2Lip ~3.7 s per 6 s scene). Contact sheets viewed: mouth moves, captions timed to Whisper words, bg clean.
* [done] backend: models_creative.py, creative_options.py, creative_jobs.py, routers/photo_replica.py, video_features_api additions
  (POST /video-jobs/render, /videos/scenes/preview, /videos/{id}/creative, options on bulk/translate), hooks in jobs.py/consent.py/signing.py/files_signed.py.
  tests/test_creative.py: 13 pass with faked heavy steps.
* LivePortrait fp16 on MPS produced one garbage frame in a run; fp32 is the same speed (113 s / 50 frames) -> fp32 default + outlier-frame repair.
* NEXT: real API+worker E2E, live lipsync bg fps measurement, SDK, API.md, worker unit checks, speed work, full test run (alembic migration needed for models_creative).

## Final status (honest)
### Verified end to end (real API 8270 + lipsync 8271 + worker, frames/contact sheets viewed)
* Photo avatar: `POST /replicas/photo` -> typed dev consent -> worker: photo check + LivePortrait idle clip (192 s wall for 50 frames while the Mac was busy; 114 s alone) -> replica ready; live lipsync service served `/idle` (99 frames) and `/render` at 70-85 fps. Idle clip: jaw-open mean 0.001, blink up to 0.77, subtle head sway. A photo with an open mouth is rejected with a clear error (the mouth is frozen as in the photo).
* Backgrounds: live (replica background set via API, base rebuilt once in 4.5 s, render fps unchanged 70-85 vs 73-85) and offline (colour, gradient, image asset, blur). Contact sheets show a clean cut-out; small leftover of old background near shoulders is possible; selfie model is low-res (hair strands lost).
* Captions (bold, classic, karaoke, minimal), formats 16:9/9:16/1:1, logo, thumbnail, SRT, 3-scene slide transition video through the API: 9.0 s video, 28 s total (TTS 5.7, Whisper words 6.2, render 16), progress polled per scene (tts 0-8%, then render percent per scene), signed download of mp4/thumbnail/srt.
* Real-run bugs found and fixed: LivePortrait fp16 garbage frame (fp32 + repair), `Logo(asset_id=...)` TypeError (fakes had hidden it), caption chunk-merge bug (found by creative_checks).
* Tests: test_creative.py (13), test_creative_workers.py (2: runs workers/creative_checks.py, 68 asserts, + option-list sync); migrations/jobs/api/features suites pass.

### Speed
Base prep cached: 1.55 s vs 3.9-4.4 s cold per background variant. Wav2Lip ~0.6 s per second of video. Not done: parallel TTS/Whisper, batching across scenes.

### NOT verified / unfinished
* Persona-level background (only replica- and video-level); no per-conversation override in the live protocol.
* 9:16 from 720p webcam footage is soft (source pixels ~230 px wide); photo avatars from small photos also soft. `restore: sr` option untested in the creative path.
* Photo-avatar consent does not bind the photo to the speaker (no face match): someone could consent and upload another person's photo. Needs face verification before production. Voice-match is skipped for photo replicas (edit in routers/consent.py).
* Only English captions/Whisper tested; transitions dip/cut untested visually; webhooks not enriched with thumbnail URL.
* Licences: LivePortrait code MIT but its InsightFace buffalo_l detector weights are non-commercial research only (commercial use NOT allowed without replacing/licensing); Wav2Lip weights non-commercial research; MediaPipe selfie segmentation + face landmarker Apache-2.0 (commercial OK); faster-whisper base MIT (commercial OK); Kokoro Apache-2.0; PIL fonts are macOS system fonts (use DejaVu/Liberation in Docker).
* Shared files edited (small): jobs.py hooks, consent.py, signing.py, files_signed.py, SDKs, API.md.
