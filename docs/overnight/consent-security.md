# Consent binding + security wave 2 (agent "sec", Oct 3 2026)

Nothing committed. Own ports api 8410 / web 3410 / video 8812 (stopped afterwards); shared 8000/8100/3000 untouched.

## 1. Face-to-consent binding
**Flow.** `ConsentRecorder.tsx` records ONE selfie video+audio clip while the person reads the challenge phrase and POSTs it to
`/v1/replicas/{id}/consent/audio` (same endpoint, same file). Server: ASR + code words + voice match (unchanged), then
`facematch.verify`: frames sampled at 5 fps, YuNet detects, SFace embeds, median over selfie frames of best cosine vs the
reference faces (photo replica: the photo; video replica: 1 fps frames of the first 90 s of the training video, which also ties
the face to the voice that is matched from the same clip). Passive liveness on the same frames. Then the face video is DROPPED:
stored evidence = audio-only copy + sha256 of the original upload + scores (`facebinding` table, `models_sec.py`). Reference
embeddings are cached encrypted (secretbox) in `consent/<rid>/ref_face_*.enc`, removed on consent revoke and replica/account delete.
`VOCALFACE_CONSENT_FACE_MATCH=enforce|warn|off` (default enforce in production, warn in dev); `VOCALFACE_CONSENT_LIVENESS` same;
`VOCALFACE_FACE_MATCH_THRESHOLD` (0.45). Production also re-checks at the worker gate (`safety.require_consent` needs a `match` binding).
Typed consent stays dev-only and never creates a binding (so production refuses it twice).
Failure codes: `face_no_video`, `face_no_face`, `face_mismatch`, `face_no_reference`, `face_unavailable` (503, fail closed), `liveness_failed`.

**Licences.** YuNet `face_detection_yunet_2023mar.onnx` (MIT) and SFace `face_recognition_sface_2021dec.onnx` (Apache-2.0), both
OpenCV Zoo, run via OpenCV 4.11 in `workers/.venv-face` (`workers/face_embed.py`); files in `models/` (git-ignored, ~39 MB).
Not used: insightface buffalo (non-commercial weights). OpenCV is Apache-2.0.

**How thresholds were measured** (`backend/scripts_face_match_eval.py`, 47 clips). Genuine: frame pairs inside the same clip >=3 samples
apart (pose/expression change), owner video segment 0-30 s vs 30-60 s, MuseTalk sun image vs sun video (n=7859 frame pairs).
Impostor: every pair of different identities across 25 third-party example photos/videos shipped in workers/LivePortrait and
workers/MuseTalk (n=123582 pairs; measurement only, not redistributed). Results: genuine median 0.855, 5th pct 0.64; impostor median
0.07, 99th pct 0.29, 99.9th pct 0.36. The only impostor pairs above 0.45 were pairs that are visibly the same subject (Mona Lisa
photo vs its example, a source/driving pair), i.e. my labels, not model errors. OpenCV's own LFW threshold is 0.363; I chose
0.45 (frame FAR ~0.02% incl. those mislabels, frame FRR ~4%, almost all non-frontal/blurred frames; clip-level median-of-best removes most).
**Limits:** one real identity (the owner) plus a handful of third-party clips; no LFW/VGG-scale data was used (no licensed offline set);
no demographic breakdown (SFace error rates differ across groups), no relatives/twins/look-alikes, no phone-camera or low-light data.
The threshold is conservative, not tuned. Calibrate on your own consenting users before launch.

**Liveness (passive heuristic, NOT certified, no ISO 30107 / iBeta test).** Four numbers from the selfie clip: a face visible in
>=60% of frames (>=8 usable), landmark non-rigid motion (>=0.8), mouth-width/jaw motion (>=4.0, % of inter-ocular distance), and the
registered mouth/chin change (ECC homography aligns consecutive face patches, then measures residual; >=0.045). Real talking footage
(owner + 18 LivePortrait driving/source clips): mouth_motion >=5.6, nonrigid >=1.1, residual >=0.052. Simulated attacks: still image
(0.007), cv2 hand-held photo (0.013), perspective-tilted photo (0.013), ffmpeg hand-shaken photo with temporal noise (0.03; the landmark
metrics alone were fooled here: nonrigid 1.37 / mouth 6.7, which is why the registered residual was added). Nearly motionless
talkers (two MuseTalk avatar clips, residual 0.027-0.036) are rejected: false rejects are possible for people who barely move.
Tried and dropped: audio-video lip-sync correlation (5-landmark jaw proxy vs audio loudness, r -0.16..0.25 for true audio and
-0.08..0.22 for shifted audio: no separation).

**What it stops:** a different person than the photo/training video; a photo, printed or on a phone, held/tilted/shaken in front of the
camera; a recording with no or several faces; voice from person B over video of person A is NOT stopped by the face check alone
(see below). **What it does not stop:** replay of a real video of the person on a screen (passes: it is a live-looking face, tested
indirectly: real footage passes); a real-time deepfake/virtual camera injected into getUserMedia; look-alikes/relatives above 0.45;
a consenting person who is NOT the face shown but uploads their own photo as the "source" (the binding proves the selfie matches
the source, so the source must be a photo of the person being consenting, which is the intent; it cannot prove the person is who
they claim). Voice-A over face-B splicing: the same clip is used for ASR, voice and face, but nothing checks the mouth follows the audio.

## 2. Security wave 2
* **SSRF** (`netguard.py`). One httpx transport for all user-supplied URLs: resolve once, refuse non-global addresses (loopback,
  RFC1918, link-local/metadata 169.254.169.254, CGNAT, ULA, multicast, reserved, unspecified, IPv4-mapped/6to4 forms), pin the connection
  to the validated IP (Host header + TLS SNI/cert kept) so DNS rebinding cannot swap it, redirects re-checked per hop (relative redirects
  too), http(s) only. Default ON in production (`VOCALFACE_BLOCK_PRIVATE_URLS` explicit 1/0 wins; dev keeps localhost). Wired into:
  consent-time training video/photo fetch (`consent_verify.fetch_train_video`), worker + listening-clip downloads (`jobs.fetch_video`, also
  no local paths in production), webhook creation + delivery, job callback URL, tool webhooks, custom-LLM base_url, creative asset URL
  (through fetch_train_video), photo_url at creation. Not guarded on purpose: operator-configured internal services (Ollama, lip-sync, LiveKit).
  Not covered: `workers/` helper scripts that download model weights from fixed URLs (operator-controlled).
* **Deletion.** Fixed real gaps found by the new every-table/every-dir test: knowledge docs survived account deletion; thumbnails/captions
  (`videos/<id>.jpg|.srt|.wav`, scene work dirs) survived replica deletion; uploaded creative assets (files) survived account deletion;
  stored perception frames (`perception/<conversation>`) were never removed; owned workspaces/members/invites, video batch items and
  API-key scopes were missed. Now covered, plus `facebinding`/`apikeyscope`. Test builds a row in EVERY table (schema-driven, fails when a
  new table has no recognised owner column) and files in every data dir for two accounts, deletes one, asserts zero rows/files left for it and
  that the other account and the data-dir secret files survive.
* **Secret scanning.** Log records from any logger (uvicorn access log with `?api_key=`, exceptions) are scrubbed at record creation
  (`hardening.install_log_scrubbing`); `settings.redact` now also masks `api_key=`/`token=`/`sig=`/`secret=`/`password=` and Bearer values.
  Found and fixed a bug in my first version (scrubbing the template ate `%s`). Tests: keys never in responses (shown once at creation),
  `/metrics`, audit, or captured logs.
* **API key scopes.** `POST /v1/keys {scope: "full"|"read"}` (`apikeyscope` table; no existing column touched). Read keys: GET only
  (403 `read_only_key` on any write, except `/v1/files/sign` and `/v1/moderation/check`). Legacy signup key is always full. Not enforced on
  WebSocket paths (they need a conversation created by a write anyway).
* **CORS/CSRF review.** Allow-list only (production refuses `*`), no credentials header, auth is the `x-api-key` header (no cookies, so
  classic CSRF does not apply; a cross-site form post has no key). Tests assert evil origin gets no ACAO, no `set-cookie`. WebSocket does not
  check Origin (auth is the key in the query string, which is now scrubbed from logs but can still sit in proxy logs).
* **Dependency audit.** `pip-audit -r requirements.txt`: no known vulnerabilities. `npm audit --omit=dev` (web): 2 (1 critical, 1 high), all
  PostCSS advisories inside `next/node_modules/postcss` (build-time CSS stringify/sourcemap issues); fix needs `next@16.3.8` (breaking
  upgrade), NOT applied. Not a runtime server exposure, but track it.

## 3. Verification
* Backend: 392 passed; failing: `test_migrations::test_fresh_upgrade_matches_create_all` (new tables incl. mine `facebinding`, `apikeyscope`;
  orchestrator generates the migration) and `test_templates[hr-onboarding-buddy]` (another agent's work in progress).
* New test files: `test_sec_netguard.py` (35), `test_sec_face.py` (23, 4 of them with the real models on real footage), `test_sec_deletion.py` (5),
  `test_sec_misc.py` (6).
* Real Chromium (Playwright, `--use-fake-device-for-media-stream` with mjpeg camera file + Kokoro phrase wav, real Whisper, real SFace) against
  api 8410 / web 3410, enforce mode, replica whose training video is the owner's founder video: different person rejected `422 face_mismatch`;
  a still photo fed as the camera rejected (but with `face_no_face`, not `liveness_failed`: that particular still frame was a poor face, so
  the liveness path itself is proven by the pytest real-model tests, not by Chromium); owner accepted (face score 0.929, liveness pass,
  residual 0.103, 60 frames). Fake camera = file loop, so this is not a real webcam test. No phone browsers tested.

## 4. Still unprotected (honest list)
Screen replay of the real person's video; real-time injected deepfake camera; look-alikes/relatives; voice/face splicing without lip-sync
check; WebSocket rate limiting and message size; per-process rate limits; no email verification/captcha on signup (bot signup); `localStorage`
API key in the dashboard (XSS = key theft); DNS pinning only covers the guarded fetchers; no certified liveness; no malware scanning of uploads;
Postgres/S3 deployments of reference templates untested; face thresholds measured on one identity.
