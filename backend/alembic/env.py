"""Alembic environment: uses the app's own engine URL (VOCALFACE_DB_URL) and the full SQLModel metadata."""
from alembic import context

from app import migrate

config = context.config
migrate.load_all_models()
target_metadata = migrate.metadata()


def _url() -> str:
    return config.attributes.get("url") or migrate.db_url()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        from sqlalchemy import create_engine

        connection = create_engine(_url()).connect()
        own = True
    else:
        own = False
    try:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        if own:
            connection.commit()
    finally:
        if own:
            connection.close()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
