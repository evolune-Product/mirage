# Personal LoRA trial: SoulX-FlashHead Lite on the owner's own footage

Question: does a LoRA fine-tune of FlashHead Lite on the owner's own, consented footage improve identity fidelity and/or lip-sync versus the base model on held-out audio?
Scope: this Mac only (M1 Pro, 32 GB, MPS), time-boxed. Nothing pushed, no spend, no external accounts, base weights untouched.

**Verdict (short): inconclusive-to-negative. The training loss on held-out audio fell 14%, but the rendered clips did not get measurably better with a photo reference, and got clearly worse (blurry, artefacts) when the reference was a frame from the same video. No lip-sync gain is demonstrable. Do not ship "train a replica" on this recipe.**

## What was built (workers/lora_trial/, all own code, no peft)
- `prep_data.py`: MediaPipe face box, crop side 2.3x face width, centre +0.12 face height, 512x512, 25 fps, Gaussian-smoothed box; time split.
- `lora.py`: minimal LoRA (`LoRALinear`, zero-init B, fp32 adapters on a frozen bf16 base). Targets: self-attn q/k/v/o, audio cross-attn q/k/v/o in all 30 blocks, and the 4 `audio_proj` linears. Rank 8, alpha 8.
- `train.py`: own training loop. FlashHead Lite is a 4-step distilled flow model sampled at fixed shifted timesteps (1000, 750, 500, 250 -> 1000, 937, 833, 625). I train exactly there: `x_t=(1-t)x0+t*eps`, `x0hat = x_t - t*v` (the sampler's own update), MSE on x0 over the non-clamped latent frames. Conditioning mirrors the sampler: 2 clean motion latent frames clamped, reference-image latent as `y` (random frame from a pool of 27 stills drawn from the training footage), wav2vec window embeddings built the same way as streaming inference (last 8 s, zero front pad). LTX-VAE latents and audio embeddings are pre-cached. Gradient checkpointing per block.
- `render.py`: base or LoRA render through the unmodified pipeline code (same chunking/seed 42 as `generate_video.py` stream mode). `evalpair.py`: metrics (below).

## Numbers
| item | value |
|---|---|
| trainable params | 7,565,312 (0.50% of 1,526,864,256 frozen) across 244 linears |
| schedule | 120 optimizer updates x 4 micro-steps = 480 forward/backward passes, AdamW lr 1e-4 (5-update warmup, then constant), grad clip 1.0 |
| wall clock | training 112 min; cache build (VAE encode of windows) ~32 min; ~13 s per micro-step with checkpointing |
| peak memory | 5.26 GB MPS driver memory in training (so (a) never triggered); 7.2 GB for rendering |
| data | 63.8 s of ONE person: founder video 46.6 s (1166 frames) + demo_face_v2 17.2 s (430 frames) = 192 windows of 33 frames (stride 8), 27 reference stills. Held out: last 10.0 s of the founder speech (250 frames), plus a 1 s gap dropped. |
| held-out latent loss (x0 MSE, 10 windows x 4 timesteps, fixed noise) | base 0.2476 -> u20 0.2424 -> u40 0.2322 -> u60 0.2231 -> u80 0.2169 -> u100 0.2144 -> u120 0.2124 |

Note: the held-out windows were used only to monitor, and I did not pick a checkpoint from them (I evaluated the last one). The latent loss is the one clean win; it only says the model predicts this person's latents on that audio slightly better in a one-step teacher-forced setting.

## Rendered comparison (held-out 10 s speech, seed 42, base vs LoRA u120)
Metrics follow `/tmp/vocalface_demo/evalface.py` (SFace cosine to the reference, jaw range, lip-audio corr, sharpness, jitter) plus PSNR/SSIM of the 512 crop against the REAL held-out footage crop (own gaussian-window SSIM; face crop = central 320 px).

| condition | identity cos | jaw range | lip-audio corr (weak) | jaw corr vs REAL | sharpness | jitter | PSNR / SSIM vs real |
|---|---|---|---|---|---|---|---|
| photo ref (wa_tight), BASE | 0.917 | 0.237 | 0.017 | 0.591 | 58.6 | 0.0044 | 9.97 / 0.326 |
| photo ref, LoRA u40 | 0.918 | 0.235 | -0.038 | 0.593 | 59.6 | 0.0035 | 9.97 / 0.326 |
| photo ref, LoRA u120 | 0.916 | 0.184 | 0.090 | 0.633 | 73.3 | 0.0018 | 9.96 / 0.314 |
| clone_b.wav, photo ref, BASE | 0.910 | 0.216 | 0.107 | n/a | 55.5 | 0.0050 | n/a |
| clone_b.wav, photo ref, LoRA u120 | 0.920 | 0.201 | -0.023 | n/a | 64.5 | 0.0021 | n/a |
| video-frame ref (train frame), BASE | 0.767 | 0.328 | 0.128 | 0.606 | 12.5 | 0.0094 | 15.71 / 0.579 |
| video-frame ref, LoRA u120 | 0.385 | 0.298 | 0.038 | 0.542 | 8.2 | 0.0042 | 17.22 / 0.614 |
| video-frame ref, LoRA u120 at 0.5 strength | 0.438 | 0.303 | -0.091 | 0.598 | 7.1 | 0.0070 | 16.93 / 0.618 |

Reading it honestly:
- **Photo reference (the real product case): identity is unchanged** (0.917 vs 0.916; 0.910 vs 0.920 on the second audio, within noise for one seed). The LoRA clip moves less (jaw range down 22%, jitter halved) and is a bit sharper. Lower motion can be read as "calmer" or as "less expressive / less lip movement"; I cannot tell which from numbers. **PSNR against real footage is meaningless here** (~10 dB) because the photo background (stone wall, blue shirt) differs from the Zoom backdrop of the real footage; it is reported for completeness only.
- **Lip-sync: no demonstrable gain.** The lip-audio correlation is a weak proxy (jaw opening vs RMS energy), is near zero for all clips, and changes sign between runs. The only mildly positive signal is jaw-trajectory correlation with the real footage (0.591 -> 0.633 for photo ref), one seed, one 10 s clip, not significant.
- **Video-frame reference: clearly worse by eye** (stills in `~/Desktop/VocalFace_lora_trial/stills_gt_base_lora_baseVref_loraVref.png`): blurrier face, pale blotch artefacts on the shirt, identity cosine 0.767 -> 0.385. The base is already weak here (the reference is a 2x-upscaled, blurry Zoom frame, sharpness 12.5), so this is a fragile regime, but it is the in-domain regime the LoRA was trained on and it still got worse. PSNR/SSIM rose slightly (17.2 vs 15.7 dB), which just reflects a smoother image matching real footage better; it is not a quality gain. Halving the LoRA strength did not repair it.
- Likely cause (hypothesis, untested): exposure bias. Training used clean ground-truth motion latents; at inference the next chunk's motion frames come from the model's own (VAE re-encoded) output, so small errors compound over 11 chunks, which a teacher-forced loss does not see. Fixing it needs multi-chunk rollouts in training, or noise/degradation augmentation on the motion latents.
- Abort rule (b) ("clearly worse than base on held-out audio") is met for the video-frame-reference condition and not for the photo condition. I stopped after one full recipe (one lr, one rank, one seed) rather than burning the remaining time on a sweep; the evidence did not point at a quick fix.

## What I cannot judge
I cannot watch video: everything above is from stills (frames 60 and 160 of the held-out clip) and the metrics. Lip-sync quality, expression, "does it look like him in motion", and flicker are not judged. The blind-test clips are the way to get a human verdict.
SFace identity is a coarse metric (it does not see small likeness shifts), one seed per cell, one person, one 10 s held-out clip, no confidence intervals. Source footage is a 720x404 Zoom recording, so the face in the 512 crop is upscaled about 2.2x and soft; the model can only be fine-tuned toward that softness, which may be part of why video-reference results got blurrier.

## Blind test
`~/Desktop/VocalFace_blind_test`: c5 = base, c6 = LoRA u120, same held-out founder speech (not the sentence used by c1-c4), same seed, photo reference wa_tight. Both rendered. Playwright check: 6 videos, no page errors, key/labels consistent. Clips: `~/Desktop/VocalFace_lora_trial/{base,lora}.mp4` (plus `*_vref.mp4`). Adapters live in `workers/SoulX-FlashHead/lora/` (git-ignored), not committed.

## What this implies
- **A personal LoRA is a per-replica artefact, not a general model.** It was trained on ~64 s of one person; it says nothing about generalising to other faces, and a 7.6M-parameter adapter (about 30 MB fp32) would be stored per customer. Nothing here shows that a LoRA is needed: with a decent photo, base FlashHead Lite already gives 0.91 identity cosine.
- **"Train a replica" as a product feature is not supported by this trial.** With this recipe the best case is neutral on the photo path. The identity and sync gains one would sell are not there; fine-tuning would need the rollout-aware objective above and a proper blind evaluation first. Separately, it needs consent capture per person and storage/deletion of per-person weights (biometric data).
- **Cost, rough and extrapolated (not measured on a GPU):** this run is ~480 passes plus ~190 VAE encodes. On a rented A100/H100 class GPU (about 10-15x faster than this Mac, an assumption) that is roughly 10-20 minutes, i.e. around $0.5-1 at ~$2-4/h, plus a minute to load and a few minutes for evaluation renders. A more serious run (rank 16, 2-3k passes, multi-chunk rollout, several minutes of footage, a few seeds) is more like 2-4 GPU-hours, roughly $5-15 per replica. The expensive part is not compute but the evaluation loop and the human blind tests.
- **Fine-tuning at scale** (a shared model over many consented identities) is a different, much larger project: the released weights are distilled few-step, so serious training would likely retrain/redistill from the teacher checkpoint (`pretrained` model type exists in the pipeline), which is a multi-GPU, multi-day job, not a LoRA.

## Next steps (if pursued)
1. Rollout-aware training (feed the model its own re-encoded motion frames, 2-3 chunks) and re-test the video-reference case; if it is still worse, stop.
2. Train with photo references plus background-preserving loss masks, since the product input is a photo, not a clip from the same footage.
3. Higher-resolution source footage (this Zoom clip is soft), 5+ minutes, several seeds, and a real blind test with c5 vs c6 before any decision.
4. Try lower lr / earlier checkpoints (u40 was indistinguishable from base, u120 changes motion amplitude); the useful window, if any, is in between.

Reproduce: `workers/.venv-face/bin/python workers/lora_trial/prep_data.py`; `workers/.venv-flash/bin/python workers/lora_trial/train.py --updates 120 --accum 4 --lr 1e-4 --tag r8`; `workers/.venv-flash/bin/python workers/lora_trial/render.py OUT --lora r8_u120.pt name:ref.png:audio.wav`; `workers/.venv-face/bin/python workers/lora_trial/evalpair.py ...`.

**Owner's verdict (Oct 5), watching the four clips by eye:** base with a video-frame reference (`base_vref.mp4`) = excellent; base with a photo reference (`base.mp4`) = good, could be better; both LoRA clips = not good. So the metrics were misleading for the video-reference case (the identity score compared against a photo, not the video face), the LoRA is dropped, and the best FlashHead input is a tight frame from real video. Product change: `workers/face_crop.py` + `render_flashhead.py` now crop tightly around the face (face about half the frame) before FlashHead, for photos and video frames alike.
