# Face quality (overnight, Oct 3 2026, M1 Pro 34 GB)

Agent: face-quality. Scope: lip-sync engine evaluation, compositing, source hygiene, listening clip, device-agnostic
service, GPU Dockerfile, demo video. Nothing was committed or pushed. The shared servers (8000/8100/3000) were not touched.

## TL;DR
* Wav2Lip stays the live engine. The visible "smeared / double mouth / pale chin smear" was mostly **not** 96 px resolution.
  It was (a) a base clip that still had a talking jaw (the old motion heuristic picked a window with jaw-open 0.19, above the
  video average 0.15), (b) a fixed box that did not follow the head, (c) a mask that spilled onto chin and neck.
  Fixing those gave a clean single mouth at the same speed.
* MuseTalk v1.5 runs on this Mac (MPS) but at **3-5 fps** and is softer and colour-shifted on this 720p footage. Not usable
  live here; kept as an optional offline/GPU engine (`render_offline.py --engine musetalk`). GFPGAN is too slow (1.8 s/frame) and hallucinates lip outlines.
  Real-ESRGAN (tiny, 4.9 MB) gives a small, artifact-free sharpness gain at ~6 ms/frame: offline "quality" option.
* New: face tracking + smoothed box, overlay/label/black-bar removal, jaw-open based calm window, listening-clip endpoint,
  speech-gated mouth blend, prepare/health/perf logging, device auto-select, Dockerfile.gpu.
* Live browser flow verified end to end (Playwright, fake mic): no errors, 1.2-2.3 s from user transcript to first lip-synced frame.

## Measurements (all repeated; Mac is shared and noisy)

| Item | Result |
|---|---|
| Wav2Lip inference, steady state, MPS | 150-175 fps (batch 32). Cold first batch ~1-1.8 s, now removed by startup warm-up |
| Per-frame composite + JPEG (576x324) | 3-5 ms (was ~5.6 ms/frame in w2l_bench); end-to-end `/render` ~250 ms per 1 s of audio (about 4x real time), `/health` perf: 46-57 fps average incl. cold renders |
| Mediapipe FaceLandmarker (CPU) | 12 ms/frame; all 1441 frames of the founder video detected |
| Cold base prep (new replica, 40 s of footage) | ~8-9 s (was 16-19 s before tracking every 2nd frame); cached on disk afterwards (~1.5 s reload) |
| Real-ESRGAN general-x4v3 on the 96 px output | ~6 ms/frame on MPS, +4 MB weights, small gain, no halos with sharpen 0-0.4 |
| GFPGAN 1.4 (ONNX, CoreML EP) | 1.8 s/frame. Rejected (also invents a lip outline) |
| MuseTalk v1.5 (fp16/fp32 on MPS, bs 10) | 4.2-4.6 s per 20 frames = 4-5 fps for UNet+VAE decode; 3.0 fps for the whole run (150 frames in 50 s incl. VAE encode 10 s). 25 fps needs a real GPU |
| Closed-mouth window quality (jaw-open, mediapipe blendshape) | old heuristic: mean 0.190 (max 0.41); new: mean 0.042 (max 0.21); video mean 0.153 |
| Idle -> speaking head pop if phase is not continued | mean abs pixel diff 5.8 (random phase pair) vs 0.9 between consecutive frames |

Disk: workers/.venv-face 1.6 GB, workers/MuseTalk 3.7 GB (3.6 GB weights), workers/models 10 MB (landmarker + SR weight). 110 GB free after.

## Visual verdict (contact sheets / 4x mouth crops viewed)
* Old pipeline: double mouth (generated lips over a base jaw that is mid-word), pale smeared seam along chin, Zoom name tag visible.
* New Wav2Lip pipeline: single clean mouth, natural closed-mouth base, chin/neck untouched, label gone, head-following box.
  Still soft at close range and with a faint pale outline on the upper lip (Wav2Lip-GAN trait).
* +Real-ESRGAN: marginally crisper, no new artifacts at sharpen<=0.4 (sharpen 0.6+SR gave a halo ring around the lips: avoided).
* MuseTalk: more natural mouth width and visible teeth, but whole-face VAE softness, brighter mouth zone vs. face, 8x slower than real time. On a
  1080p+ source on a CUDA GPU it would likely be the better "quality" engine; **not verified here**.
* Poisson blending (`--seamless`) was tried: slight warm colour shift and per-frame variation risk, no clear win; left as an option, alpha blend is the default.

## What changed (files)
workers/ (all new or rewritten, owned by me):
* `facelib.py`: FaceTracker (mediapipe landmarks + blendshapes, CPU delegate), Gaussian temporal smoothing, static overlay detector (label boxes and glyph text, merged rects),
  black-bar trimming (`content_rect`), `locate_face_region` (tile search for small facecam insets), `choose_crop`, `calm_window` (jaw-open + peak + blink + motion),
  landmark-shaped soft mouth mask clipped to the face oval, LAB colour match, gated sharpen, composite.
* `face_render.py`: `prepare_base` (cache in `<replica>/base_v3/{frames,base.mp4,meta.json}`; listening clip preferred), per-frame model boxes (fixed size, smoothed centre),
  `Wav2LipEngine` (+warm-up), `speech_alpha` (generated mouth fades to the real closed mouth in silence), ping-pong cursor, `paste`.
* `lipsync_server.py` rewritten: device `auto|cuda|mps|cpu` (`MIRAGE_LIPSYNC_DEVICE`), engine env, `/health` (device, torch, mps/cuda, tracker, load times, per-replica base info, rolling fps),
  per-request perf logging, `/prepare/{id}`, `/invalidate/{id}`, `?phase=` and `?fade_in=` on `/render`, startup preload, soft mouth entry when a reply starts. Falls back to a fixed Haar box
  if mediapipe is not importable (the shared `workers/.venv` does not have it, see below).
* `render_offline.py` (offline generation, `--engine wav2lip|musetalk`, `--restore none|sr|gfpgan`, `--listening`), `musetalk_engine.py`, `restore.py`, `face_checks.py`
  (9 plain-assert unit checks), `requirements-lipsync.txt`, `Dockerfile.gpu`.
* `workers/.venv-face` (python 3.11, mediapipe 0.10.21 + torch + diffusers): separate on purpose. `workers/MuseTalk` = shallow clone + `models/` weights.
* Untouched: `run_worker.py`, `extract_face.py`, `find_idle.py`, `w2l_bench.py` (still work; the new code replaces find_idle's job).

backend/ (mine): `app/models_face.py` (table `listeningclip`, existing tables unchanged), `app/routers/listening_clip.py` (auto-registered),
`app/pipeline/lipsync.py` (client gains `prepare()`, `health()`, `render(pcm, phase=None, fade_in=False)`; old call signature unchanged),
tests `test_face_listening.py` (3), `test_face_lib.py` (runs face_checks), `test_face_client.py`. Full suite: 95 passed.

### Listening clip API
`POST /v1/replicas/{id}/listening-clip {"url": "..."}` (http(s), file://, or path; 1-120 s, <=200 MB, ffprobe-validated, owner only, replaces atomically),
`GET` metadata, `DELETE`. Stored at `<data>/replicas/<id>/listening.mp4`; the router tells the lipsync service to drop its cache (`/invalidate`, best effort).
When present it is the idle loop **and** the speaking base (up to 10 s, no calm-window search); otherwise the jaw-open heuristic runs on the source.
Verified through the real API: clip registered, lipsync `/idle` then served `listening: true`, 125 frames.

## Verified
* Unit/API tests (95 pass). Offline renders of the founder video (15 s demo) with all pipeline stages, frames inspected as contact sheets.
* Isolated stack (api 8011, lipsync 8110, video 8711): replica -> listening clip -> idle/render; full Playwright live conversation (fake mic, WebGL flags), run 3 times: no page errors,
  canvas pixels change while the agent speaks (screenshots differ), 1.2 / 2.3 / 3.4 s transcript->first lip frame (3.4 s was before warm-up was added).
* Second footage type: the 1080p screen recording with a facecam inset (face ~90 px): face found by tile search, native-res pre-crop, black bar removed. Output is only ~230x130 px
  because that is all the pixels the source has; head top is clipped there (edge case).
* Haar fallback path (shared venv, no mediapipe) serves idle + render.

## Could NOT verify
* CUDA path and `Dockerfile.gpu` (no NVIDIA here; written, never built). MuseTalk quality/speed on a CUDA GPU or 1080p+ footage.
* Real microphone/speakers; subjective lip-sync accuracy beyond viewing stills (no numeric sync metric such as LSE-C/D was computed).
* Idle -> speaking continuity in the real playground: the client ping-pongs the idle loop on `performance.now()`, so the server cannot know its phase (see protocol note).
* Long-run stability (hours), concurrent conversations (bases/engine are per-process, requests serialised by a lock).
* A true closed-mouth listening clip: the footage has none; a 5 s low-jaw cut of the same video was used to test the plumbing. Even the calmest window is slightly parted (jaw mean 0.04-0.06).

## Protocol notes for the session.py / playground owners (I did not edit those)
1. `/idle` response now also has `w`, `h` (frames are face-centred crops, e.g. 576x324, not the source size; segments match).
2. To remove the idle->speech head pop: playground should send its current idle ping-pong index as `?phase=` on the first `/render` of a reply (client formula
   `k = floor(now*fps) % (2n-2)`) via `LipsyncClient.render(pcm, phase=k, fade_in=True)`, and resume the idle loop from `end_phase` after speech. Response carries `start_phase`, `end_phase`, `loop_len`.
3. Call `LipsyncClient.prepare()` when a conversation is created (or a replica becomes ready): cold base prep takes ~8 s, cached ~1.5 s.
4. Install mediapipe into `workers/.venv` for full quality (not done: it would change numpy/protobuf in a venv the running servers use):
   `workers/.venv/bin/pip install --no-deps mediapipe==0.10.21 && workers/.venv/bin/pip install "protobuf<5" absl-py attrs sentencepiece`
   (mediapipe 1.0.x aborts on macOS with a Metal "Service is unavailable" fatal error; 0.10.21 works). Without it the service uses the old fixed Haar box and no jaw-based window choice.
   Note: an early `pip install mediapipe` into `workers/.venv` briefly upgraded numpy/opencv there; I reverted it to numpy 1.26.4 / opencv 4.10 and verified imports (torch, librosa, scipy, cv2).

## Demo
`/tmp/mirage_demo_face.mp4`: 17.2 s, 568x320, H.264 + AAC. Kokoro `af_heart` speech (14.8 s) with 1.2 s of silence either side, Wav2Lip + Real-ESRGAN (sharpen 0.4), tracked
overlay-free crop of the founder's own video (`SpendVeto_Founder_Video_1min.mp4`), 4 s calm base loop. Command:
`MIRAGE_IDLE_SECONDS=4 .venv-face/bin/python workers/render_offline.py --source <video> --audio <wav> --out out.mp4 --restore sr --sharpen 0.4`.

## Next steps
1. Wire items 2-3 above into session.py/playground; ask the owner to record a 5 s closed-mouth listening clip (the biggest remaining realism gain: the base mouth is never fully shut).
2. Install mediapipe in the shared venv (note 4) and restart the 8100 service.
3. On a rented NVIDIA box: build `Dockerfile.gpu`, then benchmark MuseTalk (expect 25+ fps) and decide whether to offer it as the high-quality tier; try a 256 px Wav2Lip variant if one with a licence you can use appears.
4. Mouth-region super-resolution trained on the person (or a LoRA on MuseTalk) is the real fix for close-up softness; the source resolution (720p, 137 px face) caps all of these.
5. Per-frame lip colour/teeth consistency check (MuseTalk mouth was too light), and a numeric sync metric (SyncNet confidence) for regression tests.
