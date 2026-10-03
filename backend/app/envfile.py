"""Minimal .env loader (no dependency): fills variables that are NOT already set in the environment.
Looks at $MIRAGE_ENV_FILE, then ./.env, then <repo>/.env. Secrets are never printed."""
import os
from pathlib import Path


def load() -> None:
    if os.environ.get("MIRAGE_LOAD_DOTENV") == "0":
        return
    cands = [os.environ.get("MIRAGE_ENV_FILE", ""), ".env", str(Path(__file__).resolve().parents[2] / ".env")]
    for c in cands:
        p = Path(c) if c else None
        if not p or not p.is_file():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if " #" in v and not v.startswith(("'", '"')):
                v = v.split(" #", 1)[0].strip()
            v = v.strip("'\"")
            if k and v != "" and k not in os.environ:
                os.environ[k] = v
        return  # first file found wins
