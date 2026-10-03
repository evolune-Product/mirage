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
