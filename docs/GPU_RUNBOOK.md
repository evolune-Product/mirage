# Renting a GPU for an evening: real-time FlashHead test

Goal: find out, with real numbers, whether SoulX-FlashHead runs in real time on one rented NVIDIA GPU, and what a user-minute costs.
**Untested script:** `infra/gpu_flashhead_setup.sh` was written on a Mac with no CUDA. Expect small fixes.

## Before you rent (costs real money; you decide)
- Pick a provider with hourly billing and an Ubuntu 22.04 + CUDA 12.x image (RunPod, Vast.ai, Lambda). An RTX 4090 (24 GB) is the card the FlashHead README quotes: Lite 96 fps / 3 concurrent real-time streams; Pro 10.8 fps (needs 2x RTX 5090 for real time). Those are the authors' numbers, not ours.
- Expect roughly $0.3-$1/hour for a 4090 (check current prices). One evening of testing is a few dollars.
- Stop the instance when done. Idle GPUs bill.

## Steps (about 1 hour including the ~14 GB download)
1. SSH in, `git clone https://github.com/evolune-Product/vocalface && cd vocalface`
2. `bash infra/gpu_flashhead_setup.sh` (optionally pass your face PNG and a 16 kHz speech WAV).
3. Read the per-step timings. Real time means a chunk of video is produced faster than it plays (25 fps).
4. `scp` flashhead_lite.mp4 / flashhead_pro.mp4 back and compare with the Mac renders.

## What to write down
GPU model, Lite and Pro seconds per generated second, peak VRAM (`nvidia-smi`), and concurrent streams before slowdown. Put them in `docs/UNIT_ECONOMICS.md` replacing the assumed $0.007-0.022/user-minute.

## Then wire it into the live path (not built yet)
FlashHead's streaming mode (`gradio_app_streaming.py`) yields chunks. VocalFace's live protocol sends `video_segment` frames; a new engine in `workers/engines/` would wrap the streaming pipeline. That is real work (days) and only worth it if step 3 shows real time.

## Licence reminder
README says Apache-2.0 (code+weights). Bundled VAE and wav2vec2 have their own licences; have counsel confirm before selling. See `docs/LICENSES.md`.
