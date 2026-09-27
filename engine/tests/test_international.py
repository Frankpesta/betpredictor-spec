"""International results ingest (docs/09, docs/discovered/international-results.md)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.config import get_settings
from engine.db.models import League, Match, Team
from engine.db.session import session_scope
from engine.ingest import football_data as fd
from engine.ingest import international as intl
from engine.jobs import sync_leagues

HEADER = "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n"
CSV = (
    HEADER
    + "2018-06-14,Russia,Saudi Arabia,5,0,FIFA World Cup,Moscow,Russia,FALSE\n"  # before window
    + "2025-09-05,Honduras,Nicaragua,2,0,Friendly,Tegucigalpa,Honduras,FALSE\n"
    + "2026-03-20,Honduras,Nicaragua,1,1,CONCACAF Nations League,Tegucigalpa,Honduras,FALSE\n"
    + "2026-06-20,Brazil,Morocco,2,1,FIFA World Cup,New York,United States,TRUE\n"
    + "2026-06-20,Brazil,Morocco,2,1,FIFA World Cup,New York,United States,TRUE\n"  # dup
    + "2026-06-21,Monaco,Jersey,0,3,Friendly,Monaco,Monaco,FALSE\n"
    + "2026-06-22,Spain,Chile,x,1,Friendly,Madrid,Spain,FALSE\n"
)
SINCE = date(2019, 7, 1)


def test_parse_results() -> None:
    p = intl.parse_results(CSV.encode(), SINCE)
    assert p.rows_read == 7 and p.before_window == 1
    assert p.skipped == {"duplicate_same_day": 1, "no_score": 1}
    wc = next(r for r in p.rows if r.home == "Brazil")
    assert wc.neutral and wc.tournament == "FIFA World Cup"
    assert wc.kickoff_utc == datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def test_parse_rejects_changed_header() -> None:
    with pytest.raises(ValueError, match="neutral"):
        intl.parse_results(b"date,home_team,away_team,home_score,away_score\n", SINCE)


@pytest.mark.parametrize(
    ("d", "season"), [(date(2026, 6, 20), "2025-26"), (date(2026, 7, 1), "2026-27")]
)
def test_season_of(d: date, season: str) -> None:
    assert intl.season_of(d) == season


def _leagues(s: Session) -> tuple[League, League]:
    sync_leagues(s, get_settings())
    s.flush()
    by_key = {lg.key: lg for lg in s.scalars(select(League))}
    return by_key["INTL"], by_key["LIGUE1"]


def test_upsert_repeat_pairs_clash_rename_and_idempotency(migrated_db: Path) -> None:
    parsed = intl.parse_results(CSV.encode(), SINCE)
    with session_scope(migrated_db) as s:
        intl_lg, ligue1 = _leagues(s)
        s.add(Team(league_id=ligue1.id, canonical_name="Monaco"))  # the club
        s.flush()
        c1 = intl.upsert_results(s, intl_lg, parsed)
    assert (c1.inserted, c1.renamed_clashes) == (4, ["Monaco"])
    with session_scope(migrated_db) as s:
        names = set(s.scalars(select(Team.canonical_name)))
        assert {"Monaco", "Monaco (national team)", "Jersey", "Brazil"} <= names
        hon = s.scalars(
            select(Match)
            .join(Team, Team.id == Match.home_team_id)
            .where(Team.canonical_name == "Honduras")
        ).all()
        # same pair, same season, two fixtures: allowed via fixture_key
        assert sorted(m.fixture_key for m in hon) == ["2025-09-05", "2026-03-20"]
        assert {m.season for m in hon} == {"2025-26"}
        wc = s.scalars(select(Match).where(Match.neutral.is_(True))).one()
        assert wc.home_goals == 2 and wc.status == "finished"
        intl_lg, _ = _leagues(s)
        c2 = intl.upsert_results(s, intl_lg, parsed)
        assert (c2.inserted, c2.updated, c2.unchanged) == (0, 0, 4)
        assert s.scalar(select(func.count()).select_from(Match)) == 4


def test_result_matches_sportybet_fixture_within_a_day(migrated_db: Path) -> None:
    """A fixture created from SportyBet (exact UTC kickoff, next UTC day) gets the result."""
    with session_scope(migrated_db) as s:
        intl_lg, _ = _leagues(s)
        usa = Team(league_id=intl_lg.id, canonical_name="United States")
        mex = Team(league_id=intl_lg.id, canonical_name="Mexico")
        s.add_all([usa, mex])
        s.flush()
        kickoff = datetime(2026, 10, 11, 1, 30, tzinfo=UTC)  # 21:30 local on the 10th
        s.add(
            Match(
                league_id=intl_lg.id, season="2026-27", kickoff_utc=kickoff, home_team_id=usa.id,
                away_team_id=mex.id, status="scheduled", fixture_key="2026-10-11",
                sportybet_event_id="sr:match:1",
            )
        )  # fmt: skip
        s.flush()
        csv = HEADER + "2026-10-10,United States,Mexico,1,2,Friendly,Dallas,United States,FALSE\n"
        c = intl.upsert_results(s, intl_lg, intl.parse_results(csv.encode(), SINCE))
        assert (c.inserted, c.updated) == (0, 1)
        m = s.scalars(select(Match)).one()
        assert (m.home_goals, m.away_goals, m.status) == (1, 2, "finished")
        assert m.kickoff_utc == kickoff  # SportyBet's exact kickoff is kept


def test_football_data_refuses_to_reuse_a_national_team(migrated_db: Path) -> None:
    with session_scope(migrated_db) as s:
        intl_lg, ligue1 = _leagues(s)
        s.add(Team(league_id=intl_lg.id, canonical_name="Georgia"))
        s.flush()
        csv = "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG\nF1,01/09/2025,18:00,Georgia,Lyon,1,0\n"
        with pytest.raises(ValueError, match="INTL"):
            fd.upsert_file(s, ligue1, "2025-26", fd.parse_csv(csv.encode(), "F1"))


def test_consecutive_day_repeat_is_a_new_match_not_an_update(migrated_db: Path) -> None:
    csv = (
        HEADER
        + "2025-11-14,Grenada,Barbados,1,0,Friendly,St George's,Grenada,FALSE\n"
        + "2025-11-15,Grenada,Barbados,2,2,Friendly,St George's,Grenada,FALSE\n"
    )
    with session_scope(migrated_db) as s:
        intl_lg, _ = _leagues(s)
        c = intl.upsert_results(s, intl_lg, intl.parse_results(csv.encode(), SINCE))
        assert (c.inserted, c.updated) == (2, 0)
        scores = sorted(
            (m.fixture_key, m.home_goals, m.away_goals) for m in s.scalars(select(Match))
        )
        assert scores == [("2025-11-14", 1, 0), ("2025-11-15", 2, 2)]


def test_non_fifa_sides_are_excluded() -> None:
    csv = (
        HEADER
        + "2025-09-05,Spain,Georgia,2,0,FIFA World Cup qualification,Madrid,Spain,FALSE\n"
        + "2025-10-01,Spain,Georgia,1,0,Friendly,Madrid,Spain,FALSE\n"
        + "2025-10-02,Raetia,Canton Ticino,5,1,Friendly,Chur,Switzerland,FALSE\n"
        + "2025-10-03,Spain,Jersey,9,0,Friendly,Madrid,Spain,FALSE\n"
    )
    p = intl.parse_results(csv.encode(), SINCE)
    assert intl.keep_fifa_only(p) == {"Spain", "Georgia"}
    assert [(r.home, r.away) for r in p.rows] == [("Spain", "Georgia"), ("Spain", "Georgia")]
    assert p.skipped == {"non_fifa_team": 2}
