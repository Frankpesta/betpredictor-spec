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
import random
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
    # Optional job arguments (e.g. `book` restricted to one slip by the API's rebook).
    params: dict[str, Any] = field(default_factory=dict)

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


def create_job_run(name: str, db_path: Path | None = None) -> int:
    """Insert a `running` job_runs row; return its id (the API replies with it before running)."""
    with session_scope(db_path) as s:
        row = JobRun(job_name=name, started_at=utcnow(), status="running")
        s.add(row)
        s.flush()
        return row.id


@contextmanager
def job_run(
    name: str,
    settings: Settings | None = None,
    db_path: Path | None = None,
    run_id: int | None = None,
    params: dict[str, Any] | None = None,
) -> Iterator[JobContext]:
    settings = settings or get_settings()
    if run_id is None:
        run_id = create_job_run(name, db_path)
    ctx = JobContext(
        job_run_id=run_id,
        name=name,
        settings=settings,
        db_path=db_path,
        params=dict(params or {}),
    )
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


def _ingest(ctx: JobContext) -> None:
    """football-data history -> seed aliases -> Understat xG -> acceptance report (docs/02)."""
    from engine.ingest import checks, football_data, international, understat
    from engine.ingest.fetch import PoliteClient
    from engine.mapping.teams import apply_seed, clear_resolved, load_seed

    leagues = ctx.settings.enabled_leagues()
    with PoliteClient(ctx.settings, football_data.SOURCE) as client:
        football_data.ingest_all(ctx, client, leagues)
        ctx.count("fd_http_requests", client.requests_made)
        ctx.count("fd_http_retries", client.retries)

    if any(lg.international for lg in leagues):
        with PoliteClient(ctx.settings, international.SOURCE) as client:
            international.ingest(ctx, client)
            ctx.count("intl_http_requests", client.requests_made)

    with session_scope(ctx.db_path) as s:
        seeded = apply_seed(s, load_seed())
        clear_resolved(s)
    ctx.note("aliases_inserted", seeded.inserted)
    for m in seeded.missing_canonical:
        ctx.warn(f"seed canonical team not found: {m}")

    with PoliteClient(ctx.settings, understat.SOURCE) as client:
        understat.ingest_all(ctx, client, leagues)
        ctx.count("understat_http_requests", client.requests_made)
        ctx.count("understat_http_retries", client.retries)

    with session_scope(ctx.db_path) as s:
        report, ok = checks.acceptance_report(
            s,
            {lg.key for lg in leagues if lg.understat_key is not None},
            random.Random(),
        )
    ctx.note("acceptance_passed", ok)
    print(report)


def _map_teams(ctx: JobContext) -> None:
    from engine.mapping.teams import run_map_teams

    print(run_map_teams(ctx))


def _fit(ctx: JobContext) -> None:
    """Fit goals + xG models for every enabled league as of now (docs/03, docs/08 Phase 2)."""
    from engine.db.model_runs import fit_and_store, format_fit_summary
    from engine.db.queries import league_by_key
    from engine.ingest.fetch import current_season_start_year

    now = utcnow()
    year = current_season_start_year(now.date())
    season = f"{year}-{(year + 1) % 100:02d}"
    out: list[str] = []
    for lg in ctx.settings.enabled_leagues():
        with session_scope(ctx.db_path) as s:
            league = league_by_key(s, lg.key)
            stored = fit_and_store(s, league, ctx.settings, now)
            out.append(format_fit_summary(s, league, stored, ctx.settings, season))
            ctx.summary.setdefault("runs", {})[lg.key] = {
                "model_run_id": stored.run.id,
                "n_matches": stored.run.n_matches,
                "converged": stored.run.converged,
                "retried": stored.model.goals.retried,
                "low_confidence": len(stored.model.low_confidence),
            }
    print("\n\n".join(out))


def _backtest(ctx: JobContext) -> None:
    from engine.backtest.run import run_backtest

    print(run_backtest(ctx))


def _discover(ctx: JobContext) -> None:
    from engine.sportybet.discovery import run_discovery

    print(run_discovery(ctx))


def _odds(ctx: JobContext) -> None:
    from engine.sportybet.odds import run_odds

    print(run_odds(ctx))


def _picks(ctx: JobContext) -> None:
    from engine.db.picks import run_picks

    print(run_picks(ctx))


def _book(ctx: JobContext) -> None:
    from engine.sportybet.booking import run_book

    print(run_book(ctx))


def _book_test(ctx: JobContext) -> None:
    from engine.sportybet.booking import run_book_test

    print(run_book_test(ctx))


def _close(ctx: JobContext) -> None:
    from engine.settle.clv import run_close

    print(run_close(ctx))


def _settle(ctx: JobContext) -> None:
    from engine.settle.clv import update_leg_clv
    from engine.settle.settle import run_settle

    print(run_settle(ctx))
    with session_scope(ctx.db_path) as s:
        ctx.note("legs_clv_updated", update_leg_clv(s))


JOBS: dict[str, JobFn] = {
    "ingest": _ingest,
    "map-teams": _map_teams,
    "fit": _fit,
    "backtest": _backtest,
    "discover": _discover,
    "odds": _odds,
    "picks": _picks,
    "book": _book,
    # acceptance helper for Phase 5: books a 2-leg test selection, writes no slip
    "book-test": _book_test,
    "close": _close,
    "settle": _settle,
}
# `daily` is a composite: ingest -> odds -> picks -> book, each recorded as its own job run.
# ingest first so `picks` refits on the latest results (ensure_fresh_run, docs/05 §1.1).
DAILY_SEQUENCE = ("ingest", "odds", "picks", "book")
# A failed soft step (source down, 403/429) does not stop the day: picks run on the data
# already stored, and the failure stays visible in its own job run + the daily warnings.
DAILY_SOFT_STEPS = frozenset({"ingest"})
JOB_NAMES = (*JOBS, "daily")


def run_job(
    name: str,
    settings: Settings | None = None,
    db_path: Path | None = None,
    run_id: int | None = None,
    params: dict[str, Any] | None = None,
) -> int:
    """Run one job (or the `daily` sequence) synchronously; return its job_run id.

    `run_id` reuses a row made by `create_job_run` (the API path); `params` are passed
    to the job through `JobContext.params`.
    """
    if name not in JOB_NAMES:
        raise ValueError(f"unknown job {name!r}; choose from {', '.join(JOB_NAMES)}")
    settings = settings or get_settings()
    with session_scope(db_path) as s:
        sync_leagues(s, settings)

    if name == "daily":
        with job_run("daily", settings, db_path, run_id) as ctx:
            for step in DAILY_SEQUENCE:
                child = create_job_run(step, db_path)
                ctx.summary.setdefault("steps", {})[step] = child
                try:
                    run_job(step, settings, db_path, child)
                except Exception as exc:
                    if step not in DAILY_SOFT_STEPS:
                        raise
                    ctx.warn(f"{step} failed (job run {child}): {exc!r}; continuing on stored data")
        return ctx.job_run_id

    with job_run(name, settings, db_path, run_id, params) as ctx:
        JOBS[name](ctx)
    return ctx.job_run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="engine", description="BetPredictor engine jobs")
    parser.add_argument("job", nargs="?", choices=JOB_NAMES, help="job to run")
    parser.add_argument("--list", action="store_true", help="list available jobs")
    args = parser.parse_args(argv)
    # Reports contain non-ASCII (team names, arrows); Windows consoles default to cp1252.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
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
