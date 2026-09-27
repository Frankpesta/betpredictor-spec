"""Read-side queries that turn DB rows into the arrays the pure model code needs."""

from __future__ import annotations

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.backtest.walk_forward import ClosingOdds, MatchMarkets
from engine.db.models import HistoricalOdds, League, Match, Team
from engine.model.league import LeagueMatches


def league_by_key(session: Session, key: str) -> League:
    return session.scalars(select(League).where(League.key == key)).one()


def load_finished_matches(session: Session, league_id: int) -> LeagueMatches:
    rows = session.execute(
        select(
            Match.id,
            Match.kickoff_utc,
            Match.home_team_id,
            Match.away_team_id,
            Match.home_goals,
            Match.away_goals,
            Match.home_xg,
            Match.away_xg,
            Match.neutral,
        )
        .where(Match.league_id == league_id, Match.status == "finished")
        .order_by(Match.kickoff_utc, Match.id)
    ).all()

    def col(i: int, dtype: type) -> np.ndarray:
        return np.array(
            [np.nan if r[i] is None and dtype is float else r[i] for r in rows], dtype=dtype
        )

    return LeagueMatches(
        match_id=col(0, int),
        kickoff=np.array([r[1].timestamp() for r in rows], dtype=float),
        home=col(2, int),
        away=col(3, int),
        home_goals=col(4, float),
        away_goals=col(5, float),
        home_xg=col(6, float),
        away_xg=col(7, float),
        neutral=np.array([1.0 if r[8] else 0.0 for r in rows], dtype=float),
    )


def team_names(session: Session) -> dict[int, str]:
    return {t.id: t.canonical_name for t in session.scalars(select(Team))}


def current_season_teams(session: Session, league_id: int, season: str) -> set[int]:
    rows = session.execute(
        select(Match.home_team_id, Match.away_team_id).where(
            Match.league_id == league_id, Match.season == season
        )
    ).all()
    return {t for r in rows for t in r}


def load_match_markets(
    session: Session,
    league: League,
    seasons: list[str],
    odds_source: str,
    closing_source: str,
    closing_fallback: str,
) -> list[MatchMarkets]:
    """Finished matches of `seasons` with opening (bet) and closing (CLV) odds.

    Closing odds per market come from `closing_source` when it has the full pair,
    else from `closing_fallback` (user decision 2026-09-27: Pinnacle stops Jan 2026).
    """
    matches = session.scalars(
        select(Match)
        .where(
            Match.league_id == league.id,
            Match.season.in_(seasons),
            Match.status == "finished",
        )
        .order_by(Match.kickoff_utc, Match.id)
    ).all()
    ids = [m.id for m in matches]
    odds: dict[int, dict[tuple[str, str, str, str], tuple[float | None, float]]] = {}
    for chunk_start in range(0, len(ids), 900):  # SQLite parameter limit
        chunk = ids[chunk_start : chunk_start + 900]
        for o in session.scalars(select(HistoricalOdds).where(HistoricalOdds.match_id.in_(chunk))):
            odds.setdefault(o.match_id, {})[(o.bookmaker, o.timing, o.market, o.selection)] = (
                o.line,
                o.odds,
            )

    def ou(
        d: dict[tuple[str, str, str, str], tuple[float | None, float]], bm: str, timing: str
    ) -> tuple[float, float] | None:
        a, b = d.get((bm, timing, "OU", "over")), d.get((bm, timing, "OU", "under"))
        return (a[1], b[1]) if a and b else None

    def ah(
        d: dict[tuple[str, str, str, str], tuple[float | None, float]], bm: str, timing: str
    ) -> tuple[float, float, float] | None:
        a, b = d.get((bm, timing, "AH", "home")), d.get((bm, timing, "AH", "away"))
        if not (a and b) or a[0] is None:
            return None
        return (a[0], a[1], b[1])

    out: list[MatchMarkets] = []
    for m in matches:
        d = odds.get(m.id, {})
        c_ou = c_ah = None
        for src in (closing_source, closing_fallback):
            if c_ou is None and (pair := ou(d, src, "close")) is not None:
                c_ou = ClosingOdds(src, pair, None)
            if c_ah is None and (trip := ah(d, src, "close")) is not None:
                c_ah = ClosingOdds(src, None, trip)
        assert m.home_goals is not None and m.away_goals is not None
        out.append(
            MatchMarkets(
                match_id=m.id,
                league=league.key,
                season=m.season,
                kickoff=m.kickoff_utc.timestamp(),
                home=m.home_team_id,
                away=m.away_team_id,
                home_goals=m.home_goals,
                away_goals=m.away_goals,
                ou_open=ou(d, odds_source, "open"),
                ah_open=ah(d, odds_source, "open"),
                closing_ou=c_ou,
                closing_ah=c_ah,
                neutral=m.neutral,
            )
        )
    return out
