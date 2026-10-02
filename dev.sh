#!/bin/zsh
# Start everything Mirage needs locally. Ctrl+C stops all of it.
#   backend API :8000   lip-sync service :8100   worker (replicas + videos)   dashboard :3000
# Requires Ollama running (`ollama serve`) for the LLM.
cd "$(dirname "$0")"
pids=()
trap 'kill $pids 2>/dev/null; exit' INT TERM EXIT
(cd backend && .venv/bin/uvicorn app.main:app --port 8000 2>&1 | sed 's/^/[api] /') & pids+=($!)
(cd workers && PYTORCH_ENABLE_MPS_FALLBACK=1 .venv/bin/uvicorn lipsync_server:app --port 8100 2>&1 | sed 's/^/[lipsync] /') & pids+=($!)
(cd backend && while true; do .venv/bin/python ../workers/run_worker.py --once 2>&1 | grep -v '^$' | sed 's/^/[worker] /'; sleep 3; done) & pids+=($!)
(cd web && npm run dev -- -p 3000 2>&1 | sed 's/^/[web] /') & pids+=($!)
echo "Mirage starting: dashboard http://localhost:3000  api http://localhost:8000  lipsync http://localhost:8100"
wait
