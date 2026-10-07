#!/bin/sh
# Optional fast TTS engine: Kokoro-82M on Apple MLX in its own Python 3.11 venv (the backend's Python 3.14 cannot install
# mlx-audio's dependencies). ~1.4 GB on disk. Without it VocalFace silently uses the CPU/ONNX Kokoro engine.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON311:-/opt/homebrew/bin/python3.11}"
"$PY" -m venv "$ROOT/.venv-tts"
"$ROOT/.venv-tts/bin/pip" install -q mlx-audio "misaki[en]"
echo "ok: $ROOT/.venv-tts (model weights download on first start from huggingface: mlx-community/Kokoro-82M-bf16)"
