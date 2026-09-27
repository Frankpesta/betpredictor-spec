"""`make odds` storage logic on the saved listing payload (no network)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.config import LEAGUES, get_settings
from engine.db.models import League, Match, OddsSnapshot, Team, TeamAlias, UnresolvedName
from engine.db.session import session_scope
from engine.jobs import sync_leagues
from engine.mapping.teams import Resolver
from engine.sportybet import markets as mk
from engine.sportybet.odds import OddsCounts, store_event

PC = json.loads(
    (Path(__file__).parent / "fixtures" / "sportybet" / "pcEvents_epl_with_ah.json").read_text(
        encoding="utf-8"
    )
)
ARSENAL_LEEDS = mk.parse_pc_events(PC).events[0]
NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def _setup(s: Session, league_key: str = "EPL") -> League:
    sync_leagues(s, get_settings())
    s.flush()
    lg = s.scalars(select(League).where(League.key == league_key)).one()
    ids = {}
    for name in ("Arsenal", "Leeds"):
        t = Team(league_id=lg.id, canonical_name=name)
        s.add(t)
        s.flush()
        ids[name] = t.id
    s.add(TeamAlias(team_id=ids["Arsenal"], source="sportybet", alias="Arsenal"))
    s.flush()
    return lg


def _store(s: Session, lg: League, ev: mk.SbEvent, key: str = "EPL") -> OddsCounts:
    counts = OddsCounts()
    r = Resolver(s, "sportybet")
    store_event(s, lg, LEAGUES[key], ev, r, NOW, counts)
    r.flush_misses(NOW)
    return counts


def test_unresolved_team_skips_event_and_is_recorded(migrated_db: Path) -> None:
    with session_scope(migrated_db) as s:
        c = _store(s, _setup(s), ARSENAL_LEEDS)
        assert c.skipped == {"unresolved_team": 1} and c.unresolved == {"Leeds United"}
        assert s.scalar(select(func.count()).select_from(Match)) == 0
        u = s.scalars(select(UnresolvedName)).one()
        assert (u.source, u.raw_name, u.league_key) == ("sportybet", "Leeds United", "EPL")


def test_new_fixture_and_snapshots_then_rerun_links_same_match(migrated_db: Path) -> None:
    with session_scope(migrated_db) as s:
        lg = _setup(s)
        leeds = s.scalars(select(Team).where(Team.canonical_name == "Leeds")).one()
        s.add(TeamAlias(team_id=leeds.id, source="sportybet", alias="Leeds United"))
        s.flush()
        c = _store(s, lg, ARSENAL_LEEDS)
        assert (c.matches_inserted, c.events_stored) == (1, 1)
        m = s.scalars(select(Match)).one()
        assert (m.sportybet_event_id, m.season, m.status) == (
            "sr:match:72221292",
            "2026-27",
            "scheduled",
        )
        assert m.fixture_key == "" and m.kickoff_utc == ARSENAL_LEEDS.kickoff_utc
        snaps = s.scalars(select(OddsSnapshot)).all()
        assert len(snaps) == len(ARSENAL_LEEDS.odds) == c.snapshots
        home = next(x for x in snaps if (x.market, x.line, x.selection) == ("AH", -0.5, "home"))
        assert (home.sb_market_id, home.sb_specifier, home.sb_outcome_id) == (
            "16",
            "hcp=-0.5",
            "1714",
        )
        # second run: same match, new snapshot batch, kickoff moved -> updated
        moved = replace(ARSENAL_LEEDS, kickoff_utc=ARSENAL_LEEDS.kickoff_utc + timedelta(hours=2))
        c2 = _store(s, lg, moved)
        assert (c2.matches_inserted, c2.matches_updated) == (0, 1)
        assert s.scalar(select(func.count()).select_from(Match)) == 1
        assert s.scalar(select(func.count()).select_from(OddsSnapshot)) == 2 * len(
            ARSENAL_LEEDS.odds
        )


def test_finished_match_is_not_touched(migrated_db: Path) -> None:
    with session_scope(migrated_db) as s:
        lg = _setup(s)
        leeds = s.scalars(select(Team).where(Team.canonical_name == "Leeds")).one()
        arsenal = s.scalars(select(Team).where(Team.canonical_name == "Arsenal")).one()
        s.add(TeamAlias(team_id=leeds.id, source="sportybet", alias="Leeds United"))
        s.add(
            Match(league_id=lg.id, season="2026-27", kickoff_utc=ARSENAL_LEEDS.kickoff_utc,
                  home_team_id=arsenal.id, away_team_id=leeds.id, home_goals=2, away_goals=0,
                  status="finished")
        )  # fmt: skip
        s.flush()
        c = _store(s, lg, ARSENAL_LEEDS)
        assert c.skipped == {"already_finished": 1}
        assert s.scalar(select(func.count()).select_from(OddsSnapshot)) == 0


def test_intl_fixture_matches_by_date_and_uses_fixture_key(migrated_db: Path) -> None:
    with session_scope(migrated_db) as s:
        lg = _setup(s, "INTL")
        leeds = s.scalars(select(Team).where(Team.canonical_name == "Leeds")).one()
        s.add(TeamAlias(team_id=leeds.id, source="sportybet", alias="Leeds United"))
        s.flush()
        c = _store(s, lg, ARSENAL_LEEDS, "INTL")
        m = s.scalars(select(Match)).one()
        assert c.matches_inserted == 1 and m.fixture_key == "2026-10-10"
        later = replace(
            ARSENAL_LEEDS,
            event_id="sr:match:999",
            kickoff_utc=ARSENAL_LEEDS.kickoff_utc + timedelta(days=150),
        )
        c2 = _store(s, lg, later, "INTL")  # same pair months later: a separate fixture
        assert c2.matches_inserted == 1
        assert s.scalar(select(func.count()).select_from(Match)) == 2
