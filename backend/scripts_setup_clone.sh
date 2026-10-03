#!/bin/sh
# Optional voice cloning engine: Chatterbox (MIT, 23 languages) on Apple MLX in its own Python 3.11 venv (the backend's
# Python 3.14 cannot install mlx-audio's dependencies). ~2 GB incl. weights. Without it Mirage keeps the Kokoro preset
# voices and every clone request fails cleanly (status "failed", no data stored).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON311:-/opt/homebrew/bin/python3.11}"
"$PY" -m venv "$ROOT/.venv-clone"
"$ROOT/.venv-clone/bin/pip" install -q "mlx-audio==0.5.7" soundfile
# weights: downloaded from Hugging Face on first start (mlx-community/chatterbox-4bit, ~600 MB), or place a local copy
# in <repo>/models/chatterbox-4bit and set MIRAGE_CLONE_REPO to that directory.
echo "ok: $ROOT/.venv-clone"
