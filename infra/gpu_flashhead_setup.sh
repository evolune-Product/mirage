#!/usr/bin/env bash
# One-command FlashHead setup + benchmark on a rented NVIDIA box (Ubuntu 22.04, CUDA 12.x driver, >=24 GB VRAM e.g. RTX 4090).
# NOT TESTED: written on an Apple-silicon Mac without CUDA. Read docs/GPU_RUNBOOK.md first. Run from the repo root:
#   bash infra/gpu_flashhead_setup.sh [path/to/face.png] [path/to/speech.wav]
set -euo pipefail
FACE="${1:-}"; WAV="${2:-}"
FH=workers/SoulX-FlashHead
command -v nvidia-smi >/dev/null || { echo "no NVIDIA GPU/driver found"; exit 1; }
nvidia-smi --query-gpu=name,memory.total --format=csv
sudo apt-get update -y && sudo apt-get install -y ffmpeg git python3.10 python3.10-venv libgl1 >/dev/null

[ -d "$FH" ] || git clone --depth 1 https://github.com/Soul-AILab/SoulX-FlashHead "$FH"
python3.10 -m venv workers/.venv-flash-gpu
PY=workers/.venv-flash-gpu/bin/python
$PY -m pip install -U pip
$PY -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128
$PY -m pip install -r "$FH/requirements.txt"
$PY -m pip install flash_attn==2.8.0.post2 --no-build-isolation || echo "flash_attn build failed: falls back to PyTorch SDPA (slower)"
$PY -m pip install -U "huggingface_hub[cli]"
mkdir -p "$FH/models"
workers/.venv-flash-gpu/bin/huggingface-cli download Soul-AILab/SoulX-FlashHead-1_3B --local-dir "$FH/models/SoulX-FlashHead-1_3B"
workers/.venv-flash-gpu/bin/huggingface-cli download facebook/wav2vec2-base-960h --local-dir "$FH/models/wav2vec2-base-960h"

# Benchmark with the model's own example unless you pass your own face/speech.
FACE="${FACE:-$FH/examples/girl.png}"; WAV="${WAV:-$FH/examples/podcast_sichuan_16k.wav}"
for M in lite pro; do
  echo "== $M =="
  ( cd "$FH" && time $OLDPWD/$PY generate_video.py --ckpt_dir models/SoulX-FlashHead-1_3B --wav2vec_dir models/wav2vec2-base-960h \
      --model_type $M --cond_image "$OLDPWD/$FACE" --audio_path "$OLDPWD/$WAV" --audio_encode_mode stream \
      --save_file "$OLDPWD/flashhead_$M.mp4" ) 2>&1 | grep -E "denoise per step|decode video|real" | tail -8
done
echo "Outputs: flashhead_lite.mp4 flashhead_pro.mp4. Copy them back and compare. Per-step times above decide real-time viability:"
echo "  real time needs one 25 fps chunk generated faster than its duration."
