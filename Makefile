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
	cd backend && MIRAGE_ENV=production .venv/bin/python -c "from app import settings; e=settings.validate_production(); print(e or 'ok'); raise SystemExit(1 if e else 0)"
up:
	docker compose -f infra/docker-compose.yml up -d --build
down:
	docker compose -f infra/docker-compose.yml down
