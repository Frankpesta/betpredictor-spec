"""`job_runs` bookkeeping and CLI entrypoints.

The Makefile targets and the FastAPI endpoints call the same functions here.

    uv run engine <job>          # e.g. `uv run engine ingest`
    uv run engine --list

Every job runs inside `job_run(...)`, which writes a `running` row, then marks it
`success` with a JSON summary or `failed` with the traceback. Failures are never
swallowed: the exception is re-raised after being recorded.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import LEAGUES, Settings, get_settings
from engine.db.base import utcnow
from engine.db.models import JobRun, League
from engine.db.session import session_scope
from engine.logging import get_logger

log = get_logger(__name__)


@dataclass
class JobContext:
    """Handle given to a running job for counting what it did."""

    job_run_id: int
    name: str
    settings: Settings
    db_path: Path | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    def count(self, key: str, n: int = 1) -> None:
        self.summary[key] = self.summary.get(key, 0) + n

    def skip(self, reason: str, n: int = 1) -> None:
        """Count skipped/dropped rows by reason — never drop rows silently."""
        skipped: dict[str, int] = self.summary.setdefault("skipped", {})
        skipped[reason] = skipped.get(reason, 0) + n

    def note(self, key: str, value: Any) -> None:
        self.summary[key] = value

    def warn(self, message: str) -> None:
        self.summary.setdefault("warnings", []).append(message)
        log.warning(message, extra={"fields": {"job": self.name, "job_run_id": self.job_run_id}})


def _to_json(summary: dict[str, Any]) -> str:
    return json.dumps(summary, default=str, sort_keys=True)


@contextmanager
def job_run(
    name: str, settings: Settings | None = None, db_path: Path | None = None
) -> Iterator[JobContext]:
    settings = settings or get_settings()
    with session_scope(db_path) as s:
        row = JobRun(job_name=name, started_at=utcnow(), status="running")
        s.add(row)
        s.flush()
        run_id = row.id
    ctx = JobContext(job_run_id=run_id, name=name, settings=settings, db_path=db_path)
    log.info("job started", extra={"fields": {"job": name, "job_run_id": run_id}})
    try:
        yield ctx
    except BaseException as exc:
        tb = traceback.format_exc()
        with session_scope(db_path) as s:
            row = s.get_one(JobRun, run_id)
            row.status = "failed"
            row.finished_at = utcnow()
            row.summary_json = _to_json(ctx.summary)
            row.error = tb
        log.error(
            "job failed",
            exc_info=exc,
            extra={"fields": {"job": name, "job_run_id": run_id}},
        )
        raise
    else:
        with session_scope(db_path) as s:
            row = s.get_one(JobRun, run_id)
            row.status = "success"
            row.finished_at = utcnow()
            row.summary_json = _to_json(ctx.summary)
        log.info(
            "job finished",
            extra={"fields": {"job": name, "job_run_id": run_id, **_flat(ctx.summary)}},
        )


def _flat(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in summary.items() if isinstance(v, int | float | str)}


def sync_leagues(session: Session, settings: Settings) -> None:
    """Upsert the known leagues (docs/01 §2); `enabled` mirrors settings."""
    existing = {lg.key: lg for lg in session.scalars(select(League))}
    for d in LEAGUES.values():
        enabled = d.key in settings.leagues.enabled
        lg = existing.get(d.key)
        if lg is None:
            session.add(
                League(
                    key=d.key,
                    name=d.name,
                    fd_code=d.fd_code,
                    understat_key=d.understat_key,
                    enabled=enabled,
                )
            )
        else:
            lg.name, lg.fd_code, lg.understat_key = d.name, d.fd_code, d.understat_key
            lg.enabled = enabled


# ---------------------------------------------------------------------------
# Job registry. Each job body receives its JobContext.
# ---------------------------------------------------------------------------

JobFn = Callable[[JobContext], None]


def _not_built(phase: int) -> JobFn:
    def run(ctx: JobContext) -> None:
        raise NotImplementedError(
            f"job '{ctx.name}' is built in Phase {phase} (docs/08); not available yet"
        )

    return run


JOBS: dict[str, JobFn] = {
    "ingest": _not_built(1),
    "map-teams": _not_built(1),
    "fit": _not_built(2),
    "backtest": _not_built(2),
    "discover": _not_built(3),
    "odds": _not_built(3),
    "picks": _not_built(4),
    "book": _not_built(5),
    "close": _not_built(6),
    "settle": _not_built(6),
}
# `daily` is a composite: odds -> picks -> book, each recorded as its own job run.
DAILY_SEQUENCE = ("odds", "picks", "book")
JOB_NAMES = (*JOBS, "daily")


def run_job(name: str, settings: Settings | None = None, db_path: Path | None = None) -> int:
    """Run one job (or the `daily` sequence) synchronously; return its job_run id."""
    if name not in JOB_NAMES:
        raise ValueError(f"unknown job {name!r}; choose from {', '.join(JOB_NAMES)}")
    settings = settings or get_settings()
    with session_scope(db_path) as s:
        sync_leagues(s, settings)

    if name == "daily":
        with job_run("daily", settings, db_path) as ctx:
            for step in DAILY_SEQUENCE:
                child = run_job(step, settings, db_path)
                ctx.summary.setdefault("steps", {})[step] = child
        return ctx.job_run_id

    with job_run(name, settings, db_path) as ctx:
        JOBS[name](ctx)
    return ctx.job_run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="engine", description="BetPredictor engine jobs")
    parser.add_argument("job", nargs="?", choices=JOB_NAMES, help="job to run")
    parser.add_argument("--list", action="store_true", help="list available jobs")
    args = parser.parse_args(argv)
    if args.list or not args.job:
        print("\n".join(JOB_NAMES))
        return 0
    try:
        run_job(args.job)
    # CLI boundary: the failure is already recorded in job_runs and logged with its
    # traceback by job_run(); here we only turn it into a non-zero exit code.
    except Exception as exc:  # noqa: BLE001
        print(f"job '{args.job}' failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
