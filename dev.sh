#!/bin/zsh
# Start everything VocalFace needs locally. Ctrl+C stops all of it.
#   backend API :8000   lip-sync service :8100   worker (replicas + videos)   dashboard :3000
# Requires Ollama running (`ollama serve`) for the LLM.
cd "$(dirname "$0")"
export VOCALFACE_JUDGE_MODEL="${VOCALFACE_JUDGE_MODEL:-llama3.2:3b}"  # objective judging: 1.00 accuracy vs 0.75 for the 1B model (backend/evals)
pids=()
trap 'kill $pids 2>/dev/null; exit' INT TERM EXIT
(cd backend && .venv/bin/uvicorn app.main:app --port 8000 2>&1 | sed 's/^/[api] /') & pids+=($!)
LVENV=".venv"; [ -x workers/.venv-face/bin/uvicorn ] && LVENV=".venv-face"  # face tracking needs mediapipe (only in .venv-face)
(cd workers && PYTORCH_ENABLE_MPS_FALLBACK=1 $LVENV/bin/uvicorn lipsync_server:app --port 8100 2>&1 | sed 's/^/[lipsync] /') & pids+=($!)
(cd backend && while true; do .venv/bin/python ../workers/run_worker.py --once 2>&1 | grep -v '^$' | sed 's/^/[worker] /'; sleep 3; done) & pids+=($!)
(cd web && npm run dev -- -p 3000 2>&1 | sed 's/^/[web] /') & pids+=($!)
echo "VocalFace starting: dashboard http://localhost:3000  api http://localhost:8000  lipsync http://localhost:8100"
wait
