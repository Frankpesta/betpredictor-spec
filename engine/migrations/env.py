"""Alembic environment. Alembic is the only thing that creates or alters tables."""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, event

from engine.config import get_settings
from engine.db import models  # noqa: F401  (registers tables on Base.metadata)
from engine.db.base import Base
from engine.db.session import sqlite_url

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return explicit
    db_arg = context.get_x_argument(as_dictionary=True).get("db")
    db_path = Path(db_arg) if db_arg else get_settings().db_file
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite_url(db_path)


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())

    @event.listens_for(engine, "connect")
    def _wal(dbapi_conn, _rec):  # type: ignore[no-untyped-def]
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            # SQLite supports transactional DDL; one commit per upgrade is much faster.
            transactional_ddl=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
