import logging

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlmodel import SQLModel

from app import db, migrate


def _engine(tmp_path, name="m.db"):
    return create_engine(f"sqlite:///{tmp_path / name}")


def test_fresh_upgrade_matches_create_all(tmp_path):
    migrate.load_all_models()
    e = _engine(tmp_path)
    assert migrate.status(e)["state"] == "empty"
    out = migrate.upgrade(e)
    assert out["state"] == "up-to-date" and out["current"] == out["head"]
    assert migrate.schema_diff(e) == [], "alembic head must equal the SQLModel metadata (add a migration: alembic revision --autogenerate)"
    ref = _engine(tmp_path, "ref.db")
    SQLModel.metadata.create_all(ref)
    a = {t: sorted(c["name"] for c in inspect(e).get_columns(t)) for t in inspect(e).get_table_names() if t != "alembic_version"}
    b = {t: sorted(c["name"] for c in inspect(ref).get_columns(t)) for t in inspect(ref).get_table_names()}
    assert a == b


def test_existing_create_all_database_is_stamped_not_recreated(tmp_path):
    migrate.load_all_models()
    e = _engine(tmp_path)
    SQLModel.metadata.create_all(e)
    with e.begin() as c:
        c.execute(text("insert into account (id,email,api_key,credits_seconds,created_at) values ('acc_x','a@b.c','mk_1',5,'2026-01-01 00:00:00')"))
    assert migrate.status(e)["state"] == "unstamped"
    out = migrate.upgrade(e)
    assert out["state"] == "up-to-date"
    with e.connect() as c:
        assert c.execute(text("select credits_seconds from account where id='acc_x'")).scalar() == 5  # data survived


def test_legacy_db_missing_a_table_is_upgraded(tmp_path):
    migrate.load_all_models()
    e = _engine(tmp_path)
    SQLModel.metadata.create_all(e)
    with e.begin() as c:
        c.execute(text("drop table sharesession"))
    out = migrate.upgrade(e)  # missing tables are created, then the DB is stamped
    assert out["state"] == "up-to-date" and migrate.schema_diff(e) == []
    assert "sharesession" in inspect(e).get_table_names()


def test_warns_when_behind(tmp_path, caplog):
    e = _engine(tmp_path)
    migrate.upgrade(e)
    with e.begin() as c:
        c.execute(text("update alembic_version set version_num='0000'"))
    caplog.set_level(logging.WARNING, logger="mirage.migrate")
    st = migrate.warn_if_behind(e)
    assert st["state"] == "behind"
    assert "SCHEMA IS BEHIND" in caplog.text


def test_cli_check_exit_code(tmp_path, monkeypatch):
    # `check` exits non-zero on an empty db and zero after upgrade (uses db.engine, so point it at a temp file)
    e = _engine(tmp_path)
    monkeypatch.setattr(db, "engine", e)
    assert migrate.main(["check"]) == 1
    assert migrate.main(["upgrade"]) == 0
    assert migrate.main(["check"]) == 0


# ---- Postgres compatibility without a Postgres server: DDL must compile for the postgresql dialect ----
def test_ddl_compiles_for_postgresql():
    migrate.load_all_models()
    d = postgresql.dialect()
    n = 0
    for t in SQLModel.metadata.sorted_tables:
        sql = str(CreateTable(t).compile(dialect=d))
        assert "AUTOINCREMENT" not in sql.upper()
        for ix in t.indexes:
            str(CreateIndex(ix).compile(dialect=d))
        n += 1
    assert n >= 30


def test_no_oversized_ints_that_overflow_postgres_integer():
    """Postgres INTEGER is 32-bit (sqlite is 64-bit): millisecond epochs or byte counts must be BigInteger."""
    from sqlalchemy import Integer

    migrate.load_all_models()
    risky = [f"{t.name}.{c.name}" for t in SQLModel.metadata.sorted_tables for c in t.columns
             if isinstance(c.type, Integer) and any(k in c.name for k in ("epoch", "timestamp_ms", "unix_ms"))]
    assert risky == [], f"use BigInteger for: {risky}"


def test_engine_kwargs_sqlite_only_options():
    assert db.engine_kwargs("sqlite:///x.db") == {"connect_args": {"check_same_thread": False}}
    pg = db.engine_kwargs("postgresql+psycopg://u:p@h/db")
    assert "connect_args" not in pg and pg["pool_pre_ping"] is True
