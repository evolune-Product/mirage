# Common tasks. Run from the repo root.
PY ?= backend/.venv/bin/python

.PHONY: test lint web-check models check-prod up down
test:            ## backend tests
	cd backend && .venv/bin/python -m pytest -q
lint:            ## syntax/pyflakes-level lint (python -m compileall + pyflakes if installed) and TypeScript type check
	cd backend && .venv/bin/python -m compileall -q app tests && (.venv/bin/python -m pyflakes app tests 2>/dev/null || echo "pyflakes not installed: pip install pyflakes")
	cd web && npx tsc --noEmit -p .
web-check:
	cd web && npx tsc --noEmit -p .
models:          ## download the speaker-verification model (26 MB, Apache-2.0)
	curl -L -o models/wespeaker_resnet34_lm.onnx https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/main/voxceleb_resnet34_LM.onnx
check-prod:      ## refuse to start if production config is unsafe
	cd backend && VOCALFACE_ENV=production .venv/bin/python -c "from app import settings; e=settings.validate_production(); print(e or 'ok'); raise SystemExit(1 if e else 0)"
up:
	docker compose -f infra/docker-compose.yml up -d --build
down:
	docker compose -f infra/docker-compose.yml down

# ---- platform: e2e / checks (append-only section) ----
.PHONY: e2e e2e-smoke e2e-install check
e2e-install:     ## install the e2e Playwright dependency (one time)
	cd e2e && npm install --no-audit --no-fund
e2e:             ## full end-to-end suite on isolated ports (needs Ollama, Kokoro models, lipsync env, founder footage); artifacts in e2e/artifacts
	@test -d e2e/node_modules || $(MAKE) e2e-install
	cd e2e && node run.mjs
e2e-smoke:       ## fast e2e: backend + dashboard only (no GPU/models/Ollama): signup, personas, webhooks, all routes @1440/390
	@test -d e2e/node_modules || $(MAKE) e2e-install
	cd e2e && node run.mjs --smoke
check:           ## tsc + backend unit tests + e2e smoke
	cd web && npx tsc --noEmit -p .
	cd backend && .venv/bin/python -m pytest -q
	$(MAKE) e2e-smoke

# ---- intelligence: conversation + retrieval + vision evals (append-only section, agent "brain") ----
.PHONY: evals evals-quick evals-retrieval evals-vlm
EVAL_MODELS ?= llama3.2:1b,llama3.2:3b
evals:           ## full conversation-quality eval against local Ollama models (EVAL_MODELS=a,b); writes backend/evals/results/*.json
	cd backend && .venv/bin/python -m evals.run_evals --models $(EVAL_MODELS) --prompt v2
evals-quick:     ## 3 cases per suite, one model: smoke test of the harness
	cd backend && .venv/bin/python -m evals.run_evals --models llama3.2:1b --limit 3
evals-retrieval: ## hit@k / MRR for chunking + embedders + BM25 fusion (add ARGS=--combined for one index over all docs)
	cd backend && .venv/bin/python -u -m evals.retrieval_eval $(ARGS)
evals-vlm:       ## speed + OCR/scene accuracy of local vision models (VLMS=moondream,gemma3:4b)
	cd backend && .venv/bin/python -m evals.vlm_eval --models $(or $(VLMS),moondream)
