from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from engine.config import get_settings
from engine.db.models import JobRun, League
from engine.db.session import session_scope
from engine.jobs import job_run, run_job


def _runs(db: Path) -> list[JobRun]:
    with session_scope(db) as s:
        return list(s.scalars(select(JobRun).order_by(JobRun.id)))


def test_success_records_summary(migrated_db: Path) -> None:
    with job_run("demo", db_path=migrated_db) as ctx:
        ctx.count("fetched", 10)
        ctx.count("inserted", 7)
        ctx.skip("empty_row", 3)
    (run,) = _runs(migrated_db)
    assert run.status == "success"
    assert run.finished_at is not None and run.finished_at >= run.started_at
    assert json.loads(run.summary_json or "{}") == {
        "fetched": 10,
        "inserted": 7,
        "skipped": {"empty_row": 3},
    }
    assert run.error is None


def test_failure_records_traceback_and_reraises(migrated_db: Path) -> None:
    with pytest.raises(RuntimeError, match="boom"), job_run("demo", db_path=migrated_db) as ctx:
        ctx.count("fetched", 2)
        raise RuntimeError("boom")
    (run,) = _runs(migrated_db)
    assert run.status == "failed"
    assert "RuntimeError: boom" in (run.error or "")
    assert json.loads(run.summary_json or "{}") == {"fetched": 2}


def test_unbuilt_job_fails_loudly(migrated_db: Path) -> None:
    with pytest.raises(NotImplementedError, match="Phase 2"):
        run_job("fit", db_path=migrated_db)
    assert _runs(migrated_db)[-1].status == "failed"


def test_unknown_job_rejected(migrated_db: Path) -> None:
    with pytest.raises(ValueError, match="unknown job"):
        run_job("place-bets", db_path=migrated_db)


def test_leagues_synced_from_settings(migrated_db: Path) -> None:
    with pytest.raises(NotImplementedError):
        run_job("ingest", db_path=migrated_db)
    with session_scope(migrated_db) as s:
        leagues = {lg.key: lg.enabled for lg in s.scalars(select(League))}
    assert set(leagues) == {"EPL", "LALIGA", "SERIEA", "BUNDES", "LIGUE1", "CHAMP", "ERED"}
    assert {k for k, v in leagues.items() if v} == set(get_settings().leagues.enabled)
