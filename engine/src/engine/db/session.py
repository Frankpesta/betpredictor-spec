"""SQLite engine/session factory. Applies the mandatory PRAGMAs on every connection."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from engine.config import get_settings

_BUSY_TIMEOUT_MS = 5000
_engines: dict[str, Engine] = {}


def _set_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    finally:
        cur.close()


def sqlite_url(db_path: Path) -> str:
    return f"sqlite:///{db_path.resolve().as_posix()}"


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(sqlite_url(db_path))
    event.listen(eng, "connect", _set_pragmas)
    return eng


def get_engine(db_path: Path | None = None) -> Engine:
    """Cached engine for `db_path` (default: settings.general.db_path)."""
    path = (db_path or get_settings().db_file).resolve()
    key = str(path)
    if key not in _engines:
        _engines[key] = make_engine(path)
    return _engines[key]


def session_factory(db_path: Path | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(db_path), expire_on_commit=False)


@contextmanager
def session_scope(db_path: Path | None = None) -> Iterator[Session]:
    """Transactional scope: commit on success, rollback (and re-raise) on error."""
    session = session_factory(db_path)()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
