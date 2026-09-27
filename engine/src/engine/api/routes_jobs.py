"""Job endpoints (docs/07 §1).

Jobs run one at a time on a background thread guarded by a `threading.Lock`;
starting a job while another runs returns 409. The endpoints call the same
`engine.jobs.run_job` as the Makefile targets.
"""

from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from engine.config import Settings
from engine.db.base import utcnow
from engine.db.models import JobRun
from engine.db.session import session_scope
from engine.jobs import create_job_run, run_job
from engine.logging import get_logger

log = get_logger(__name__)

# `discover` is interactive (headed browser) and `book-test` is a Phase 5 helper:
# both stay CLI-only.
API_JOBS = (
    "ingest",
    "map-teams",
    "fit",
    "backtest",
    "odds",
    "picks",
    "book",
    "close",
    "settle",
    "daily",
)


class JobBusyError(RuntimeError):
    pass


class JobRunner:
    """Single background worker: at most one job at a time."""

    def __init__(self, settings: Settings, db_path: Path | None) -> None:
        self.settings = settings
        self.db_path = db_path
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def start(self, name: str, params: dict[str, Any] | None = None) -> int:
        """Create the job_runs row and start the job; raise JobBusyError if one runs."""
        if not self._lock.acquire(blocking=False):
            raise JobBusyError("another job is running")
        try:
            run_id = create_job_run(name, self.db_path)
            self._thread = threading.Thread(
                target=self._work, args=(name, run_id, params), name=f"job-{name}", daemon=True
            )
            self._thread.start()
        except BaseException:
            self._lock.release()
            raise
        return run_id

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _work(self, name: str, run_id: int, params: dict[str, Any] | None) -> None:
        try:
            run_job(name, self.settings, self.db_path, run_id=run_id, params=params)
        # Thread boundary: job_run() already recorded the failure and logged the
        # traceback; a failure before job_run() started (e.g. league sync) is
        # recorded here so the row never stays `running`.
        except Exception as exc:  # noqa: BLE001
            self._mark_failed_if_running(run_id, exc)
        finally:
            self._lock.release()

    def _mark_failed_if_running(self, run_id: int, exc: Exception) -> None:
        with session_scope(self.db_path) as s:
            row = s.get(JobRun, run_id)
            if row is not None and row.status == "running":
                row.status, row.finished_at = "failed", utcnow()
                row.error = "".join(traceback.format_exception(exc))
        log.error("api job failed", exc_info=exc, extra={"fields": {"job_run_id": run_id}})


def job_run_dict(row: JobRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_name": row.job_name,
        "status": row.status,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "summary": json.loads(row.summary_json) if row.summary_json else None,
        "error": row.error,
    }


def runner_of(request: Request) -> JobRunner:
    runner: JobRunner = request.app.state.runner
    return runner


router = APIRouter()


@router.post("/jobs/{name}")
def start_job(name: str, request: Request) -> dict[str, int]:
    if name not in API_JOBS:
        raise HTTPException(404, f"unknown job {name!r}; choose from {', '.join(API_JOBS)}")
    try:
        return {"job_run_id": runner_of(request).start(name)}
    except JobBusyError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/jobs/{job_run_id}")
def get_job(job_run_id: int, request: Request) -> dict[str, Any]:
    with session_scope(runner_of(request).db_path) as s:
        row = s.get(JobRun, job_run_id)
        if row is None:
            raise HTTPException(404, "job run not found")
        return job_run_dict(row)


@router.get("/jobs")
def list_jobs(request: Request, limit: int = Query(20, ge=1, le=200)) -> list[dict[str, Any]]:
    with session_scope(runner_of(request).db_path) as s:
        rows = s.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(limit))
        return [job_run_dict(r) for r in rows]
