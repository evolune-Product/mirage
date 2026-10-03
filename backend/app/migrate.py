"""Database migrations (Alembic) and the startup "is the DB behind?" check.

    python -m app.migrate status      # current vs head revision
    python -m app.migrate upgrade     # create/upgrade to head (stamps an old create_all() database first)
    python -m app.migrate stamp       # mark an existing database as being at head WITHOUT running anything
    python -m app.migrate check       # exit 1 if behind (for CI / deploy scripts)

Fresh install: `upgrade` creates every table. Existing sqlite file made by the old create_all(): `upgrade` detects
tables without an alembic_version row, stamps the baseline revision and then applies newer revisions (migrations are
written to be re-runnable: table creation is skipped when the table already exists).
"""
from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import sys
from pathlib import Path

from sqlalchemy import inspect, text

log = logging.getLogger("mirage.migrate")
BACKEND_DIR = Path(__file__).resolve().parents[1]
BASELINE = "0001"


def load_all_models() -> None:
    """Import db + every app/models_*.py so SQLModel.metadata knows every table (new model modules are picked up automatically)."""
    importlib.import_module("app.db")
    pkg = importlib.import_module("app")
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name.startswith("models_"):
            importlib.import_module(f"app.{m.name}")


def metadata():
    from sqlmodel import SQLModel

    return SQLModel.metadata


def db_url() -> str:
    return os.environ.get("MIRAGE_DB_URL", "sqlite:///mirage.db")


def _cfg(engine=None):
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


def head_revision() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_cfg()).get_current_head()


def current_revision(engine) -> str | None:
    insp = inspect(engine)
    if "alembic_version" not in insp.get_table_names():
        return None
    with engine.connect() as c:
        return c.execute(text("select version_num from alembic_version")).scalar()


def _has_app_tables(engine) -> bool:
    return "account" in inspect(engine).get_table_names()


def status(engine=None) -> dict:
    from . import db

    engine = engine or db.engine
    cur, head = current_revision(engine), head_revision()
    if cur == head:
        state = "up-to-date"
    elif cur is None and not _has_app_tables(engine):
        state = "empty"
    elif cur is None:
        state = "unstamped"  # tables exist (created by create_all) but alembic has never seen this DB
    else:
        state = "behind"
    return {"current": cur, "head": head, "state": state}


def schema_diff(engine=None) -> list:
    """Differences between the live database and the SQLModel metadata (empty = identical). Uses alembic's comparer."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from . import db

    engine = engine or db.engine
    load_all_models()
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), metadata())
    return [d for d in diff if not (d[0] in ("remove_table",) and d[1].name == "alembic_version")]


def _run(engine, fn):
    cfg = _cfg()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        fn(cfg)


def stamp(engine=None, rev: str = "head") -> None:
    from alembic import command

    from . import db

    engine = engine or db.engine
    _run(engine, lambda cfg: command.stamp(cfg, rev))


def upgrade(engine=None, rev: str = "head") -> dict:
    """Create or upgrade the schema. An unstamped legacy DB is stamped at the baseline first."""
    from alembic import command

    from . import db

    engine = engine or db.engine
    st = status(engine)
    if st["state"] == "unstamped":
        metadata().create_all(engine)  # adds tables that are missing; never alters existing ones
        diff = schema_diff(engine)
        if not diff:  # the old create_all() built (or now has) exactly today's schema
            log.warning("existing database matches the current models: stamping head %s", st["head"])
            _run(engine, lambda cfg: command.stamp(cfg, "head"))
            return status(engine)
        log.warning("existing database without alembic history and %d differences: stamping baseline %s then upgrading", len(diff), BASELINE)
        _run(engine, lambda cfg: command.stamp(cfg, BASELINE))
    _run(engine, lambda cfg: command.upgrade(cfg, rev))
    out = status(engine)
    left = schema_diff(engine)
    if left:
        log.warning("after upgrade the database still differs from the models (needs a new migration): %s", left[:5])
    out["remaining_diff"] = len(left)
    return out


def warn_if_behind(engine=None) -> dict | None:
    """Called at startup; never raises. Returns the status dict (or None if it could not be determined)."""
    try:
        st = status(engine)
    except Exception as e:  # noqa: BLE001
        log.warning("could not determine migration status: %s", e)
        return None
    if st["state"] == "unstamped" and os.getenv("MIRAGE_ENV", "dev").lower() not in ("prod", "production"):
        log.info("database was created by create_all (no alembic history); fine for dev. For production run `python -m app.migrate upgrade`")
    elif st["state"] in ("behind", "unstamped"):
        log.warning("DATABASE SCHEMA IS %s (current=%s head=%s): run `python -m app.migrate upgrade` before serving traffic",
                    st["state"].upper(), st["current"], st["head"])
    return st


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s"); log.setLevel(logging.INFO)
    load_all_models()
    cmd = argv[0] if argv else "status"
    from . import db

    if cmd == "status":
        print(status(db.engine)); return 0
    if cmd == "check":
        st = status(db.engine); print(st); return 0 if st["state"] == "up-to-date" else 1
    if cmd == "upgrade":
        print(upgrade(db.engine)); return 0
    if cmd == "stamp":
        stamp(db.engine); print(status(db.engine)); return 0
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
