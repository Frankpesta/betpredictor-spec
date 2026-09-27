"""Understat parsing + xG join, offline (saved sample JSON, docs/02 §2)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import get_settings
from engine.db.models import League, Match, Team, TeamAlias, UnresolvedName
from engine.db.session import session_scope
from engine.ingest import understat as us
from engine.jobs import sync_leagues
from engine.mapping.teams import Resolver

SAMPLE = (Path(__file__).parent / "fixtures" / "understat" / "EPL_2025_sample.json").read_bytes()


def test_parse_sample() -> None:
    p = us.parse_league_data(SAMPLE)
    assert p.skipped == {"malformed_match": 1}
    assert len(p.matches) == 4
    first = p.matches[0]
    assert first == us.UnderstatMatch(
        understat_id="28778",
        kickoff_utc=datetime(2025, 8, 15, 19, 0, tzinfo=UTC),  # 20:00 BST kickoff, stored UTC
        home="Liverpool",
        away="Bournemouth",
        home_goals=4,
        away_goals=2,
        home_xg=2.33007,
        away_xg=1.57303,
        is_result=True,
    )
    future = p.matches[3]
    assert not future.is_result and future.home_goals is None and future.home_xg is None


def test_parse_rejects_non_league_payload() -> None:
    import pytest

    with pytest.raises(ValueError):
        us.parse_league_data(b'{"teams": {}}')


def _setup(s: Session) -> League:
    sync_leagues(s, get_settings())
    s.flush()
    lg = s.scalars(select(League).where(League.key == "EPL")).one()
    teams = {}
    for name in ("Liverpool", "Bournemouth", "Aston Villa", "Newcastle", "Everton", "Leeds"):
        t = Team(league_id=lg.id, canonical_name=name)
        s.add(t)
        s.flush()
        teams[name] = t.id
    for alias, canon in (
        ("Liverpool", "Liverpool"),
        ("Bournemouth", "Bournemouth"),
        ("Aston Villa", "Aston Villa"),
        ("Newcastle United", "Newcastle"),
        ("Everton", "Everton"),
        ("Leeds", "Leeds"),
    ):
        s.add(TeamAlias(team_id=teams[canon], source="understat", alias=alias))
    ko = datetime(2025, 8, 15, 19, 0, tzinfo=UTC)

    def match(home: str, away: str, kickoff: datetime, hg: int, ag: int) -> Match:
        return Match(
            league_id=lg.id,
            season="2025-26",
            kickoff_utc=kickoff,
            home_team_id=teams[home],
            away_team_id=teams[away],
            home_goals=hg,
            away_goals=ag,
            status="finished",
        )

    s.add_all(
        [
            # fd says 4-2 like Understat
            match("Liverpool", "Bournemouth", ko, 4, 2),
            # fd goals disagree with Understat (0-0): fd must win
            match("Aston Villa", "Newcastle", datetime(2025, 8, 16, 11, 30, tzinfo=UTC), 1, 0),
            # finished in fd but absent from the Understat sample -> lowers join rate
            match("Everton", "Leeds", ko, 1, 1),
        ]
    )
    s.flush()
    return lg


def test_join_sets_xg_keeps_fd_goals_and_records_unresolved(migrated_db: Path) -> None:
    parsed = us.parse_league_data(SAMPLE)
    with session_scope(migrated_db) as s:
        lg = _setup(s)
        resolver = Resolver(s, "understat")
        jr = us.join_xg(s, lg, "2025-26", parsed, resolver)
        resolver.flush_misses()
    assert jr.understat_results == 3
    assert jr.joined == 2 and jr.xg_set == 2
    assert jr.goal_mismatch == 1
    assert jr.unresolved_team == 1  # Brighton v Fulham: no alias for either team
    assert jr.kickoff_diff == {"same": 2}
    assert (jr.fd_finished, jr.fd_finished_with_xg) == (3, 2)
    assert jr.join_rate is not None and abs(jr.join_rate - 2 / 3) < 1e-9

    with session_scope(migrated_db) as s:
        villa = s.scalars(select(Match).where(Match.home_goals == 1, Match.away_goals == 0)).one()
        assert (villa.home_goals, villa.away_goals) == (1, 0)  # football-data kept
        assert villa.home_xg == 0.318601 and villa.away_xg == 1.40098
        unresolved = set(s.scalars(select(UnresolvedName.raw_name)))
        # the unplayed fixture's names are recorded too
        assert {"Arsenal", "Chelsea", "Brighton", "Fulham"} == unresolved
        assert "Liverpool" not in unresolved
        assert all(
            u.source == "understat" and u.league_key == "EPL"
            for u in s.scalars(select(UnresolvedName))
        )

    # re-running changes nothing
    with session_scope(migrated_db) as s:
        lg = s.scalars(select(League).where(League.key == "EPL")).one()
        again = us.join_xg(s, lg, "2025-26", parsed, Resolver(s, "understat"))
    assert (again.xg_set, again.xg_unchanged) == (0, 2)
