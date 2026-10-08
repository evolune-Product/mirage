# Model trials: Ditto and EchoMimic V3 on the Mac (Oct 4 2026)

Question: are there other commercially licensed open talking-head models that beat SoulX-FlashHead Lite on this Mac (M1 Pro, 32 GB, MPS, no NVIDIA)?

**How to read this:** I cannot watch video. Quality statements below come from reading a handful of still frames plus the objective metrics in `/tmp/vocalface_demo/evalface.py` (jaw range, lip-audio correlation, SFace identity cosine, sharpness, head jitter). BENCHMARKS.md already notes the lip-audio correlation is a weak proxy (real footage scores about 0.14), so treat it as a sanity check, not a ranking. Nobody has watched these clips yet except via the blind-test page (c5, c6).

Inputs, identical for every model: `/tmp/vocalface_demo/wa_tight.png` (768x768; also `p2_tight.png` for Ditto), `/tmp/vocalface_demo/clone_b.wav` (16 kHz mono, 12 s). Reference: `wa_out.mp4` (FlashHead Lite, 512x512, 5-6 min).

## Results

| | FlashHead Lite (reference) | Ditto (PyTorch, MPS) | EchoMimic V3 Flash-pro (MPS) |
|---|---|---|---|
| Did it run on this Mac | yes | **yes**, full 12 s | **yes**, but only 3.24 s at 384x384 (81 frames, repo maximum per call) |
| Output | 512x512, 12.2 s | 768x768, 12.2 s | 384x384, 3.24 s |
| Wall clock | 5 to 6 min (per owner) | 337 to 340 s (includes ~35 s model load) | 867 s for 3.24 s (831 s denoise + decode, ~36 s load) |
| Wall seconds per video second | ~25 to 30 | **~28** (steady-state render alone ~10, see below) | **~267** |
| Peak memory (max RSS of the process; MPS memory is unified) | not measured | ~3.0 GB | ~17.2 GB (T5 + CLIP on CPU, DiT + VAE-encode on GPU) |
| Clip | `/tmp/vocalface_demo/wa_out.mp4` | `/tmp/vocalface_demo/ditto_out.mp4` (stock crop), `ditto_out_noif.mp4` (InsightFace-free), `ditto_out_p2.mp4` | `/tmp/vocalface_demo/echo_out.mp4` |

Objective metrics, full 12 s clips (`workers/.venv-face/bin/python`, same script as evalface.py, identity vs the input photo):

| clip | lip-audio corr (lag) | jaw range | identity cos | sharpness | head jitter |
|---|---|---|---|---|---|
| FlashHead Lite `wa_out` | 0.136 (5) | 0.213 | 0.908 | 55.4 | 0.0050 |
| Ditto, stock InsightFace crop | 0.041 (0) | 0.122 | 0.945 | 104.9 | 0.0047 |
| Ditto, MediaPipe crop (InsightFace-free) | 0.024 (0) | 0.129 | 0.947 | 114.8 | 0.0046 |
| Ditto on `p2_tight` (identity vs p2 photo) | 0.058 (0) | 0.199 | 0.957 | 79.3 | 0.0051 |

Same metrics on the first 3.24 s only (Echo's length; FlashHead and Ditto trimmed to match; 81 frames is a small sample, so correlations are noisy):

| clip | lip-audio corr (lag) | jaw range | identity cos | sharpness | head jitter |
|---|---|---|---|---|---|
| FlashHead Lite | 0.144 (-6) | 0.160 | 0.899 | 57.0 | 0.0055 |
| Ditto (MediaPipe crop) | 0.077 (4) | 0.209 | 0.955 | 109.8 | 0.0050 |
| EchoMimic V3 Flash | 0.188 (0) | 0.163 | 0.885 | 104.5 | 0.0042 |

Sharpness is not comparable across resolutions (384 vs 512 vs 768). Face was found in 100% of frames for every clip.

What I saw in stills (frames at several timestamps, side by side with FlashHead):
- Ditto: clean, sharp, background and shirt preserved at 768, face identical to the photo, mouth opens but visibly less than FlashHead, no frame in the sample showed artefacts. Whole-frame (not face-crop-only) output.
- EchoMimic V3: the most natural-looking stills of the three: real head and eyebrow movement, mouth shapes vary, skin texture good, identity slightly looser (cos 0.885). Only 3.2 s so I cannot judge long-range drift or seams.
- FlashHead: softer, face slightly smoothed, mouth movement larger.

## Honest verdict versus FlashHead

- **Neither beats FlashHead on speed on this Mac.** Ditto equals it (about 28 s of compute per video second; the project's design target of real time needs TensorRT on an NVIDIA GPU, which I did not test). EchoMimic V3 is about 10x slower (267 s per video second at 384x384, 8 steps, TeaCache on). Real time is far out of reach on Apple silicon for it; a 12 s clip would take about an hour in four unconnected 81-frame chunks (the repo has no chaining or seam handling).
- **Quality:** by stills, EchoMimic V3 > Ditto >= FlashHead on naturalness and sharpness; FlashHead has the strongest lip movement by the metrics, Ditto the weakest. This is two stills-and-proxy judgements, not a human rating. The blind-test page has c5 (Ditto) and c6 (EchoMimic V3) so the owner can rate them by eye. Note c6 is only 3.2 s and c4/c5 are 18.5 s / 12 s, so the clip lengths differ.
- **Licence is the deciding factor for Ditto** (below): as shipped it is not commercially clean. EchoMimic V3 Flash is clean on paper but too slow for the product. FlashHead stays the engine; Ditto is a candidate for the GPU path only if the LivePortrait weights are cleared or replaced.

## Licence check (every pretrained component)

### Ditto (`antgroup/ditto-talkinghead`, code Apache-2.0)
Files in `ditto_pytorch/` were MD5-compared against the LivePortrait and InsightFace releases already on disk (`workers/LivePortrait/pretrained_weights`).

| component | what it is | licence | verdict |
|---|---|---|---|
| `lmdm_v0.4_hubert.pth` | Ditto's own audio-to-motion diffusion | Apache-2.0 per repo README (the HF model card carries no licence metadata) | OK |
| `appearance_extractor.pth`, `motion_extractor.pth`, `warp_network.pth`, `decoder.pth` (= LivePortrait `spade_generator`), `stitch_network.pth` | **byte-identical to the LivePortrait release** | LivePortrait code is MIT; weights have no separate licence, but they are trained on public face-video datasets (the paper names VoxCeleb, MEAD, RAVDESS, FFHQ plus private data; from memory, not re-verified) whose terms are research-oriented, and the project builds on InsightFace | **UNCLEAR, treat as risky for commercial use.** Needs a lawyer or retraining |
| `landmark203.onnx` | **byte-identical to LivePortrait `landmark.onnx`** | same as above | **UNCLEAR** |
| `det_10g.onnx` | **byte-identical to InsightFace buffalo_l `det_10g`** | **InsightFace models: non-commercial research only** | **NON-COMMERCIAL** |
| `2d106det.onnx` | **byte-identical to InsightFace buffalo_l `2d106det`** | **non-commercial research only** | **NON-COMMERCIAL** |
| `face_landmarker.task` | Google MediaPipe Face Landmarker | Apache-2.0 | OK |
| `hubert_streaming_fix_kv.onnx` | HuBERT-style audio encoder exported to ONNX; the repo does not say which checkpoint | unknown; probably a Chinese HuBERT release under MIT, unverified | UNCLEAR, ask upstream |

**InsightFace swap: done and verified.** The InsightFace pair is only used for the first-frame crop, which needs just an eye-centre and a lip-centre point. `DITTO_NO_INSIGHTFACE=1` derives those from MediaPipe's 478-point landmarks (already in the pipeline) and does not even load the two InsightFace files. I moved `det_10g.onnx` and `2d106det.onnx` out of the checkpoint directory and the full 12 s run still completed with similar metrics (table above, "MediaPipe crop"). That removes the plainly non-commercial parts. The LivePortrait-derived weights cannot be swapped cheaply: they are the renderer.

### EchoMimic V3 (`antgroup/echomimic_v3`, models Apache-2.0)
Flash path used here (`infer_flash.py`) loads no face detector or landmark model at all (`ip_mask=None`).

| component | licence | verdict |
|---|---|---|
| `EchoMimicV3/echomimicv3-flash-pro` transformer | Apache-2.0 (HF metadata) | OK |
| `Wan2.1-Fun-V1.1-1.3B-InP` (DiT config, VAE `Wan2.1_VAE.pth`, tokenizer) | Apache-2.0 (HF metadata; Wan 2.1 base is Apache-2.0) | OK |
| `models_t5_umt5-xxl-enc-bf16.pth` (text encoder) | Google UMT5, Apache-2.0 | OK |
| `models_clip_open-clip-xlm-roberta-large-vit-huge-14.pth` (image encoder) | OpenCLIP XLM-R ViT-H-14, MIT; trained on LAION-5B-scale web data | OK on licence, standard web-data provenance caveat |
| `chinese-wav2vec2-base` (audio encoder for Flash) | MIT (HF metadata); pretrained on WenetSpeech | OK |
| Preview/app path only (not used here): `retina-face` pip package in `src/face_detect.py`, `wav2vec2-base-960h` | retina-face wrapper MIT, its weights come from a RetinaFace model trained on WIDER FACE (academic dataset); wav2vec2 Apache-2.0 | avoid the preview path commercially; Flash path avoids it |

Verdict: EchoMimic V3 Flash is the cleanest of the two on paper (no InsightFace, no LivePortrait). Ditto as shipped is not.

## What it took to run on the Mac

Patches (apply inside each clone): `workers/patches/ditto-mps.patch`, `workers/patches/echomimic-v3-mps.patch`. Clones and weights live in `workers/ditto/` and `workers/echomimic_v3/`, venvs in `workers/.venv-ditto` and `workers/.venv-echo` (python 3.11, torch 2.14, all gitignored). Downloads: about 25 GB total (Echo 22 GB, Ditto 2.1 GB, partial downloads deleted), at the cap.

Ditto (PyTorch path, not TensorRT): every `device` in the pickled config is forced to `DITTO_DEVICE` (mps); an LMDM default `cuda` was hardcoded; fp16 autocast disabled on CPU (a CPU fp16 3-D conv took minutes per frame); MediaPipe Metal delegate aborts on this setup, so the CPU delegate is forced and `mediapipe==0.10.21` is pinned (1.0.1 crashes); CUDA seeding removed; `run_ditto.sh`. The CPU path never finished a 3 s clip in 10 minutes, so MPS is required.
Timing detail: the first ~190 frames render at 1.0 to 1.8 s per frame while the audio-to-motion diffusion thread and the renderer share the machine, then ~0.41 s per frame once the diffusion has finished (about 10 s per video second). Another process on the Mac (the dev stack) may also have been using the GPU, so these are contended numbers. Putting the diffusion on CPU did not help. Not run: the model's streaming/online mode.

EchoMimic V3 (Wan 1.3B based, `infer_flash_mps.py`): complex RoPE replaced by real cos/sin arithmetic (frequency table stays on CPU, cached per grid), float64 removed from the timestep embedding, CUDA-only device defaults and CUDA autocast replaced by MPS autocast plus an explicit dtype cast on the patch embedding, flash-attn not needed (the repo falls back to SDPA), T5 (11 GB) and CLIP (5 GB) run once on CPU and their embeddings are cached to disk then freed from the run, VAE decode moved to CPU after the DiT (decoding 81 frames on MPS hit the unified-memory ceiling and aborted after a 7 minute denoise), `transformers` pinned below 5 and `diffusers==0.35.2` (the newer versions break the custom wav2vec2 hidden-state output). fp16 weights because M1 has no native bf16: 13.6 s/step versus 21 s/step in a smoke test. Settings: 8 steps, TeaCache on, UniPC sampler, 384x384 (the repo default is 768x768, 4x the tokens, not attempted), 81 frames (maximum per call), seed 43.
Per-step cost at 384x384 with 81 frames was 35 to 76 s (TeaCache skips some steps); 33 frames took 185 s for 1.32 s of video (about 140 s per video second).

## Not done

- `p2_tight` through EchoMimic V3 (about 15 minutes more for 3 s; skipped, time-box).
- 768x768 EchoMimic V3, longer-than-81-frame generation, Ditto streaming mode, TensorRT paths, any NVIDIA hardware.
- Any human rating beyond the blind-test page (c5 Ditto, c6 EchoMimic V3 Flash, hidden key in `~/Desktop/VocalFace_blind_test/index.html`).

**Oct 4 (later):** Ditto and EchoMimic V3 were removed (code, weights, videos, patches) at the owner's request after the blind test; this file is kept only as the record of what was measured.
