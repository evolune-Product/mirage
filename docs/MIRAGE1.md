# Mirage-1: our own audio-driven talking-face prototype (clean-room, ~1 minute of data)

Status 2026-10-04. Time-boxed prototype, about 4 h wall clock including training. Code: `workers/mirage1/`, renderer `workers/render_mirage1.py`
(same CLI as `render_flashhead.py`: `--image/--audio/--out`). Raw metrics: `docs/mirage1_eval/*.json`.

**Bottom line (honest):** the pipeline works end to end and is reusable, and the generator renders a controllable mouth. But audio-to-mouth
motion is weak: with about 1 minute of data it is far below FlashHead Lite and below Wav2Lip. Do not ship it. Value = the training
pipeline plus measured evidence of what data is needed.

I cannot watch video. Everything visual below comes from frames I read as still images (contact sheets) plus numeric metrics. The owner's
blind test (`~/Desktop/Mirage_blind_test`, clip c5) is the real judgement.

## 1. What was built
* Data prep (`data_prep.py`, `track.py`, `align.py`): MediaPipe landmarks, smoothed, similarity-aligned 128x128 face crops (eyes to a
  fixed template), 25 fps, audio features aligned per frame, time-based split (last 12% of the founder video and 15% of the demo clip are val,
  with a 25-frame gap before them). Mediapipe 1.0 in `.venv-flash` crashes, so landmarks run in `.venv-face` via subprocess.
* Generator (`model.py`): U-Net, input = lower face masked (rows below the nose hidden) + a reference frame of the same person + mask,
  conditioned by cross-attention (8x8, 16x16) and FiLM (decoder). 128 px. Losses (`train.py`): L1 (5x on the hidden region), Laplacian-pyramid L1,
  hinge patch-GAN on the mouth region (weight 0.02), optional sync-critic loss. EMA weights. Checkpoint/resume, `--overfit` sanity mode.
* Two variants:
  * **Mirage-1a (audio direct):** condition = 11-frame wav2vec2 window. 5.32 M params. 4000 steps, 63.5 min on M1 Pro MPS (batch 32), sync-critic loss on.
  * **Mirage-1b (two stage, the better one):** audio -> lip state -> generator. Lip state = 28 MediaPipe jaw/mouth blendshape scores per frame
    (used only as labels). Stage A: ridge regression from log-mel (own numpy mel, no pretrained part), 11-frame window. Stage B generator conditioned
    on lip state (4.78 M params, 5000 steps, 72.4 min, no sync loss, GT lip + noise during training, predicted lip at inference).
* Sync critic (`SyncNet`, 0.6 M): contrastive mouth-crop vs audio window. Held-out top-1 among 16 windows 25% (chance 6%), train 85%: weak, overfit.
* Inference (`render_mirage1.py`): image or base clip + wav -> mp4 with audio. Keeps the input head pose (an image gives a static head), synthesises
  only below the nose and pastes back with a soft ellipse mask. 462 frames render in about 12 s on the Mac.
* Evaluation: `evaluate.py` (lip-audio correlation, jaw range, jitter, sharpness, SFace identity cosine, jaw correlation vs real), `recon_eval.py`
  (held-out-frame reconstruction with audio ablations).

## 2. Data and consent
Training footage is only the owner's own, consented: `SpendVeto_Founder_Video_1min.mp4` (57.6 s, 720x404, real speech) and
`demo_assets/demo_face_v2.mp4` (17.2 s, 568x320, same person; I do not know whether its mouth motion is real or processed, the
glasses edges look edited). Total 74.7 s = **0.021 hours**, 1,868 frames, 1,582 train, 236 val, one identity, one background, one camera.
No VoxCeleb/HDTF/LRS. Footage stays in git-ignored `data/`.

## 3. Components and licences
| Piece | Licence | Use |
|---|---|---|
| facebook/wav2vec2-base-960h | Apache-2.0 (HF card fetched 2026-10-04; LibriSpeech audio only) | audio features for 1a and sync critic |
| MediaPipe FaceLandmarker | Apache-2.0 (not re-verified on disk) | landmarks, blendshape labels |
| PyTorch, transformers, OpenCV, numpy | BSD/Apache | libs |
| SFace / YuNet (`models/`) | evaluation only, already audited in LICENSES.md | identity metric |
| Everything else (networks, losses, mel, ridge, alignment) | written here | no code or weights from FlashHead, MuseTalk, Wav2Lip, LivePortrait, JoyVASA |

## 4. Measured results
Held-out reconstruction of the hidden lower face (val segments never trained on; PSNR dB on the hidden region, higher is better; `recon_*.json`).
Founder val / demo val:

| Condition | Founder PSNR | Demo PSNR |
|---|---|---|
| copy reference frame | 20.8 | 21.4 |
| mean train frame | 22.2 | 22.5 |
| 1a, true audio | 26.1 | 27.2 |
| 1a, shuffled audio (3 s shift) | 26.5 | 27.7 |
| 1a, zero audio | 26.4 | 26.2 |
| 1b, oracle ground-truth lip state | 30.8 | 33.8 |
| 1b, oracle lip shuffled | 28.6 | 29.8 |
| 1b, lip predicted from audio (gain 1) | 29.8 | 32.1 |
| 1b, predicted from audio, shuffled | 28.8 | 30.4 |
| 1b, zero lip (mean state) | 29.3 | 30.6 |

Reading it:
* **1a does not use audio.** True audio is no better than shuffled or zero (it is slightly worse). Reconstruction gains over the baselines come from
  the visible upper face and reference, not speech. This is the key negative result: wav2vec2 features plus 1 minute of data cannot teach audio-to-mouth.
* 1b generator **does** use the lip state: oracle lip beats shuffled lip by 2.2 to 4.1 dB, and the same generator reaches 32.4 dB val (35.1 train) at step 5000.
  Train/val gap shows memorisation of one person.
* Audio-predicted lip state beats shuffled by 1.0 to 1.7 dB and zero-lip by 0.5 to 1.6 dB: a real but small audio effect. Audio-to-lip regression on val:
  mean Pearson 0.45 over 28 parameters, jawOpen 0.64, pred std only 0.36x of real (`lipridge_metrics.json`). The ridge settings were chosen while
  looking at this ~9 s val segment, so these numbers are optimistic. The neural audio-to-lip net overfit in ~100 steps (val corr about 0.33, worse than ridge).
* Gain on predicted lips (2.5x) lowers PSNR slightly (about 0.1 to 0.3 dB) in exchange for visible motion.

Same audio as FlashHead (`a_16k.wav`, 18.5 s, owner's photo `src_sq.png`; `evalA.json`):

| | lip-audio corr | jaw range | head jitter | sharpness | identity cos |
|---|---|---|---|---|---|
| FlashHead Lite | 0.169 | 0.411 | 0.0066 | 24.5 | 0.905 |
| Wav2Lip | 0.148 | 0.227 | 0.0072 | 48.4 | 0.873 |
| Mirage-1a | 0.155 | 0.054 | 0.0010 | 63.9 | 0.968 |
| Mirage-1b | 0.052 | 0.038 | 0.0011 | 64.2 | 0.958 |

Caveats: jaw range of Mirage is about 10x smaller than FlashHead: the mouth barely moves in static-photo mode (frames I read show a nearly frozen,
half-open, blurry mouth). Sharpness and identity are inflated because most of the frame is the untouched photo and there is no head motion (jitter ~0 by construction),
so they are not wins. The lip-audio metric itself is noisy: even the real founder val clip scores only 0.08.
Base-clip mode on founder val (`evalB.json`): jaw range real 0.359, 1b 0.364, oracle 0.346; jaw correlation vs real 0.61 (predicted) and 0.92 (oracle).
Caution: here the real upper face and head pose stay visible, which leaks motion cues, so this overstates audio-driven quality.

Sanity/overfit test: 40 train frames per clip, no augmentation, 600 steps: hidden-region PSNR on those frames 21.0 -> 28.6 dB while held-out val stayed about 22.2 dB. Memorisation works, no bug in the loop.

## 5. What generalises and what overfits
Generalises (a little): coarse jaw opening from loudness/phonetic energy, lip-state to mouth rendering across unseen frames of the same person.
Overfits: everything else. One identity, one background, one lighting; the generator will not work for other people (never tested, expect failure; the owner's photo
works only because it is the same person). Fine mouth shapes, phoneme-specific visemes, teeth and tongue are blurry. 128 px upsampled to the photo size gives soft texture and a visible blur seam
at the paste ellipse. No head motion, blinking or expression: not generated.

## 6. Reproduce
```
cd workers
.venv-flash/bin/python -m mirage1.data_prep --out ../data/mirage1
.venv-flash/bin/python -m mirage1.train sync --steps 750
.venv-flash/bin/python -m mirage1.lip --ridge
.venv-flash/bin/python -m mirage1.train gen --cond lip --steps 5000 --bs 32 --tag lip --w-sync 0        # 1b, resume with --resume
.venv-flash/bin/python -m mirage1.train gen --steps 6000 --bs 32 --tag main                              # 1a
.venv-flash/bin/python render_mirage1.py --image face.png --audio a.wav --out out.mp4 --ckpt ../data/mirage1/ckpt/gen_lip.pt
.venv-flash/bin/python -m mirage1.recon_eval --ckpt ../data/mirage1/ckpt/gen_lip.pt
.venv-face/bin/python -m mirage1.evaluate --ref-img face.png name=video.mp4 ...
```
Known issues: first 1a run crashed at the GAN start (in-place discriminator update, fixed, resumed from step 500; 1a was stopped by me at step 4000 of a planned
6000 because val PSNR had flattened at about 26.6). MPS step time is about 0.9 s at batch 32 (launch-overhead bound, GPU shared with the dev stack).

## 7. What it would take to approach FlashHead (rough, not measured)
FlashHead Lite is a ~1.3 B-parameter generative video model that moves the whole head. Mirage-1 is a 5 M lower-face inpainter. Closing that gap is mostly data and compute, not code.
Assumptions (guesses from public practice, treat as order of magnitude):
* **Wav2Lip/MuseTalk-class lip sync with our own design** (lower-face, 256 px, 50 to 100 M params): about 100 to 500 hours of licensed or self-collected talking video
  from thousands of consenting speakers (not research-only sets), 1 to 2 M steps at batch 64: roughly 1,000 to 2,500 A100-hours (about 3 to 5 days on 8 GPUs),
  plus 100 to 200 A100-hours for a proper SyncNet-style critic. Estimate from our throughput: about 35 img/s on M1 Pro for 5 M params; an A100 might do 10 to 20x that for a 50 M model per image at 256 px, hence the range.
* **FlashHead-class** (whole head, expressions, diffusion): thousands of hours of video and on the order of 10,000+ GPU-hours, plus a large video backbone. Not realistic to clone;
  fine-tuning an Apache-2.0 base is the economic route.
* Cheapest meaningful next experiment on a rented GPU (about 50 to 100 GPU-hours, under 300 USD): same code, scale the data to 20 to 50 hours of consented
  multi-speaker footage, 256 px, replace the ridge audio-to-lip with a model trained on that data, and check whether audio-ablation (section 4) separates true from shuffled by several dB.
  If it does not, stop.

## 8. Next steps
1. Owner watches c5 in the blind test (it is among the five shuffled clips). Expect it to rate lowest.
2. If continuing: collect multi-speaker consented data first; model changes will not fix a 1-minute dataset.
3. Add head motion (a separate pose generator) and a higher-resolution paste-back; use the audio-ablation table as the go/no-go metric.
