from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, StatementError

from engine.db.base import Base
from engine.db.models import JobRun
from engine.db.session import get_engine, session_scope, sqlite_url

from .conftest import alembic_config

EXPECTED_TABLES = {
    "leagues",
    "teams",
    "team_aliases",
    "matches",
    "historical_odds",
    "model_runs",
    "predictions",
    "odds_snapshots",
    "value_legs",
    "slips",
    "slip_legs",
    "backtest_runs",
    "job_runs",
    "unresolved_names",
}


def test_all_tables_created(migrated_db: Path) -> None:
    tables = set(inspect(get_engine(migrated_db)).get_table_names())
    assert tables - {"alembic_version"} == EXPECTED_TABLES


@pytest.mark.filterwarnings("ignore:.*expression-based index")
def test_orm_matches_migrations(migrated_db: Path) -> None:
    eng = create_engine(sqlite_url(migrated_db))
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    eng.dispose()
    assert diff == []


def test_historical_odds_natural_key_unique_even_without_line(migrated_db: Path) -> None:
    eng = get_engine(migrated_db)
    with eng.begin() as conn:
        conn.execute(
            text("INSERT INTO leagues (key, name, fd_code, enabled) VALUES ('L','L','L',1)")
        )
        conn.execute(text("INSERT INTO teams (league_id, canonical_name) VALUES (1,'A'),(1,'B')"))
        conn.execute(
            text(
                "INSERT INTO matches (league_id, season, kickoff_utc, home_team_id,"
                " away_team_id, status)"
                " VALUES (1, '2024-25', '2024-08-17 14:00:00', 1, 2, 'finished')"
            )
        )
    insert_1x2 = text(
        "INSERT INTO historical_odds (match_id, bookmaker, timing, market, line, selection, odds)"
        " VALUES (1, 'B365', 'open', '1X2', NULL, 'home', 1.9)"
    )
    with eng.begin() as conn:
        conn.execute(insert_1x2)
    with pytest.raises(IntegrityError), eng.begin() as conn:
        conn.execute(insert_1x2)


def test_every_foreign_key_is_indexed(migrated_db: Path) -> None:
    insp = inspect(get_engine(migrated_db))
    for table in EXPECTED_TABLES:
        indexed = {ix["column_names"][0] for ix in insp.get_indexes(table) if ix["column_names"]}
        for fk in insp.get_foreign_keys(table):
            assert fk["constrained_columns"][0] in indexed, (table, fk)


def test_required_extra_indexes(migrated_db: Path) -> None:
    insp = inspect(get_engine(migrated_db))
    assert ["kickoff_utc"] in [ix["column_names"] for ix in insp.get_indexes("matches")]
    assert ["match_id", "captured_at"] in [
        ix["column_names"] for ix in insp.get_indexes("odds_snapshots")
    ]


def test_pragmas(migrated_db: Path) -> None:
    with get_engine(migrated_db).connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_check_constraint_enforced(migrated_db: Path) -> None:
    with pytest.raises(IntegrityError), session_scope(migrated_db) as s:
        s.add(JobRun(job_name="x", started_at=datetime.now(UTC), status="exploded"))


def test_foreign_keys_enforced(migrated_db: Path) -> None:
    with pytest.raises(IntegrityError), get_engine(migrated_db).begin() as conn:
        conn.execute(text("INSERT INTO teams (league_id, canonical_name) VALUES (999, 'Ghost FC')"))


def test_naive_datetime_rejected(migrated_db: Path) -> None:
    naive = datetime(2026, 1, 1)  # noqa: DTZ001 - deliberately naive
    with pytest.raises(StatementError, match="naive datetime"), session_scope(migrated_db) as s:
        s.add(JobRun(job_name="x", started_at=naive, status="running"))


def test_timestamps_roundtrip_as_utc(migrated_db: Path) -> None:
    lagos_noon = datetime.fromisoformat("2026-09-26T12:00:00+01:00")
    with session_scope(migrated_db) as s:
        row = JobRun(job_name="x", started_at=lagos_noon, status="running")
        s.add(row)
        s.flush()
        rid = row.id
    with session_scope(migrated_db) as s:
        got = s.get_one(JobRun, rid).started_at
    assert got == datetime(2026, 9, 26, 11, 0, tzinfo=UTC)
    assert got.tzinfo is UTC


def test_downgrade_and_upgrade(tmp_path: Path) -> None:
    db = tmp_path / "rt.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    eng = create_engine(sqlite_url(db))
    assert set(inspect(eng).get_table_names()) <= {"alembic_version"}
    command.upgrade(cfg, "head")
    assert set(inspect(eng).get_table_names()) >= EXPECTED_TABLES
    eng.dispose()


def test_fixture_key_allows_repeat_intl_pairs_but_not_club_repeats(migrated_db: Path) -> None:
    eng = get_engine(migrated_db)
    ins = (
        "INSERT INTO matches (league_id, season, kickoff_utc, home_team_id, away_team_id,"
        " fixture_key) VALUES (1, '2025-26', '2025-09-01 12:00:00', 1, 2, :k)"
    )
    with eng.begin() as conn:
        conn.execute(
            text("INSERT INTO leagues (key, name, fd_code, enabled) VALUES ('INTL','I',NULL,1)")
        )
        conn.execute(text("INSERT INTO teams (league_id, canonical_name) VALUES (1,'A'),(1,'B')"))
        conn.execute(text(ins), {"k": "2025-09-01"})
        conn.execute(text(ins), {"k": "2026-03-20"})  # same pair, same season, other date: ok
        conn.execute(text(ins), {"k": ""})  # club-style row
    with pytest.raises(IntegrityError), eng.begin() as conn:
        conn.execute(text(ins), {"k": ""})  # a second club-style row for the pair: rejected
    with eng.begin() as conn:
        row = conn.execute(text("SELECT neutral FROM matches LIMIT 1")).scalar_one()
    assert row == 0


def test_downgrade_to_0001_and_back(tmp_path: Path) -> None:
    db = tmp_path / "dg.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001")  # through 0004, 0003 and 0002
    eng = create_engine(sqlite_url(db))
    cols = {c["name"] for c in inspect(eng).get_columns("matches")}
    eng.dispose()
    assert "neutral" not in cols and "fixture_key" not in cols
    command.upgrade(cfg, "head")
