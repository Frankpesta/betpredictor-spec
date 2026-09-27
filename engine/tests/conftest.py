from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config

from engine.config import ROOT
from engine.db.session import get_engine, sqlite_url
from engine.logging import configure_logging

# Keep test logs out of data/logs.
configure_logging(log_dir=Path(tempfile.mkdtemp(prefix="bp-test-logs-")))

ENGINE_DIR = ROOT / "engine"


def alembic_config(db_path: Path) -> Config:
    cfg = Config(str(ENGINE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ENGINE_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", sqlite_url(db_path))
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def _template_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Migrate once per session; tests get a copy (a real `alembic upgrade head`)."""
    db = tmp_path_factory.mktemp("template") / "template.db"
    command.upgrade(alembic_config(db), "head")
    return db


@pytest.fixture
def migrated_db(_template_db: Path, tmp_path: Path) -> Iterator[Path]:
    """A fresh SQLite DB at alembic head."""
    db = tmp_path / "test.db"
    shutil.copyfile(_template_db, db)
    yield db
    get_engine(db).dispose()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never touch live sites; use httpx.MockTransport instead."""

    def refuse(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"network access in tests is forbidden: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
