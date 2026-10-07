# VocalFace end-to-end suite

```
make e2e-smoke   # ~1 min: backend + dashboard only (no models, GPU, Ollama)
make e2e         # full: needs Ollama (llama3.2:1b), models/kokoro + whisper, workers/.venv-face lipsync env, founder footage
make check       # tsc + pytest + e2e-smoke
node e2e/run.mjs --skip-video --only=live --keep   # flags: --smoke --skip-video --only=<step substring> --keep
```
Everything runs on **free ports with a temp DB + data dir** (never :8000/:8100/:3000): backend, Next dev server (`NEXT_DIST=.next-e2e`), lip-sync service, worker loop, a local webhook receiver and a one-file HTTP server for the training footage.

Steps: signup (UI) -> webhook endpoint -> replica + voice consent (UI; the fake microphone plays Kokoro speech of the challenge phrase, retried with other voices because TTS/ASR can confuse look-alike code words) -> worker trains -> persona + knowledge (UI) -> live conversation (fake mic question, asserts the agent answer uses the document, idle + lip-sync frames drawn, prints first-frame latency) -> video queued -> every static route at 1440 and 390 (no console/pageerror, no API 4xx/5xx, no horizontal overflow) -> guest link (page, info, live guest conversation, revoke) -> video rendered + valid mp4 -> webhooks (HMAC verified, retry on 500) -> health.

Env: `E2E_FOOTAGE` (face video, default the SpendVeto founder clip), `E2E_VIDEO_TIMEOUT_S` (600), `VOCALFACE_RENDER_FPS` (8), `E2E_SHOTS=1` (screenshot every route), `OLLAMA_URL`.
Outputs in `e2e/artifacts/` (gitignored): `logs/<service>.log`, `FAIL-*.png` for failed steps, `summary.json`. Steps whose prerequisites are missing report SKIP with the reason; blocked steps say which earlier step failed. Exit code 1 on any FAIL.
First run installs Playwright (`make e2e-install`); Chromium comes from the Playwright cache.
