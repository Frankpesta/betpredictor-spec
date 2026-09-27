"""Ingest acceptance checks (docs/02 §4), printed at the end of `make ingest`."""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from engine.ingest.understat import MIN_XG_JOIN_RATE

SPOT_CHECK_PER_LEAGUE = 5
# Join-rate shortfalls the user reviewed and accepted (never add one without asking).
ACCEPTED_XG_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("LIGUE1", "2026-27"): "Understat home/away swap PSG v Rennes, accepted by user 2026-09-27 "
    "(docs/discovered/understat.md)",
}


@dataclass(frozen=True)
class SeasonCount:
    league: str
    season: str
    matches: int
    finished: int
    with_xg: int
    odds_rows: int

    @property
    def xg_rate(self) -> float | None:
        return self.with_xg / self.finished if self.finished else None


def season_counts(session: Session) -> list[SeasonCount]:
    rows = session.execute(
        text(
            """
            SELECT l.key, m.season, count(*),
                   sum(m.status = 'finished'),
                   sum(m.status = 'finished' AND m.home_xg IS NOT NULL),
                   (SELECT count(*) FROM historical_odds o
                      JOIN matches m2 ON m2.id = o.match_id
                     WHERE m2.league_id = l.id AND m2.season = m.season)
            FROM matches m JOIN leagues l ON l.id = m.league_id
            GROUP BY l.key, m.season ORDER BY l.key, m.season
            """
        )
    )
    return [SeasonCount(*r) for r in rows]


def duplicate_matches(session: Session) -> int:
    """Groups sharing the natural key (league, season, home, away, fixture_key) or an fd_row_hash.

    fixture_key is '' for clubs (one home meeting per season) and the match date for INTL.
    """
    by_key: int = session.execute(
        text(
            """
            SELECT count(*) FROM (
              SELECT 1 FROM matches
              GROUP BY league_id, season, home_team_id, away_team_id, fixture_key
              HAVING count(*) > 1)
            """
        )
    ).scalar_one()
    by_hash: int = session.execute(
        text(
            """
            SELECT count(*) FROM (
              SELECT 1 FROM matches WHERE fd_row_hash IS NOT NULL
              GROUP BY fd_row_hash HAVING count(*) > 1)
            """
        )
    ).scalar_one()
    return int(by_key) + int(by_hash)


def duplicate_odds(session: Session) -> int:
    return int(
        session.execute(
            text(
                """
                SELECT count(*) FROM (
                  SELECT 1 FROM historical_odds
                  GROUP BY match_id, bookmaker, timing, market,
                           CASE WHEN market = 'OU' THEN line END, selection
                  HAVING count(*) > 1)
                """
            )
        ).scalar_one()
    )


def unresolved_names(session: Session) -> list[tuple[str, str | None, str]]:
    return [
        (r[0], r[1], r[2])
        for r in session.execute(
            text(
                "SELECT source, league_key, raw_name FROM unresolved_names "
                "ORDER BY source, league_key, raw_name"
            )
        )
    ]


def spot_check(session: Session, league_key: str, rng: random.Random) -> list[str]:
    rows = session.execute(
        text(
            """
            SELECT m.season, m.kickoff_utc, h.canonical_name, a.canonical_name,
                   m.home_goals, m.away_goals, m.home_xg, m.away_xg,
                   (SELECT odds FROM historical_odds o WHERE o.match_id = m.id
                      AND bookmaker='B365' AND timing='open' AND market='OU'
                      AND line=2.5 AND selection='over'),
                   (SELECT odds FROM historical_odds o WHERE o.match_id = m.id
                      AND bookmaker='B365' AND timing='open' AND market='OU'
                      AND line=2.5 AND selection='under')
            FROM matches m
            JOIN leagues l ON l.id = m.league_id
            JOIN teams h ON h.id = m.home_team_id
            JOIN teams a ON a.id = m.away_team_id
            WHERE l.key = :k AND m.status = 'finished'
            """
        ),
        {"k": league_key},
    ).all()
    picks = rng.sample(rows, min(SPOT_CHECK_PER_LEAGUE, len(rows)))

    def f(x: float | None, fmt: str) -> str:
        return "-" if x is None else format(x, fmt)

    return [
        f"{r[0]} {str(r[1])[:16]}Z {r[2]} {r[4]}-{r[5]} {r[3]}  "
        f"xG {f(r[6], '.2f')}-{f(r[7], '.2f')}  B365 O/U2.5 {f(r[8], '.2f')}/{f(r[9], '.2f')}"
        for r in sorted(picks, key=lambda r: str(r[1]))
    ]


def acceptance_report(
    session: Session, xg_leagues: set[str], rng: random.Random
) -> tuple[str, bool]:
    """Human-readable report + whether every docs/02 §4 check passed."""
    lines: list[str] = []
    ok = True
    counts = season_counts(session)
    lines.append("league  season   matches finished  with_xG  xG_rate   odds_rows")
    for c in counts:
        rate = "-" if c.xg_rate is None else f"{c.xg_rate:.1%}"
        flag = ""
        if c.league in xg_leagues and c.xg_rate is not None and c.xg_rate < MIN_XG_JOIN_RATE:
            accepted = ACCEPTED_XG_EXCEPTIONS.get((c.league, c.season))
            if accepted:
                flag = f"  (below 98%, accepted: {accepted})"
            else:
                flag = "  <-- below 98%"
                ok = False
        lines.append(
            f"{c.league:<7} {c.season}  {c.matches:>7} {c.finished:>8} {c.with_xg:>8} "
            f"{rate:>8} {c.odds_rows:>11}{flag}"
        )
    dups, dup_odds = duplicate_matches(session), duplicate_odds(session)
    unresolved = unresolved_names(session)
    ok = ok and dups == 0 and dup_odds == 0 and not unresolved
    lines += [
        "",
        f"duplicate matches: {dups}",
        f"duplicate historical_odds: {dup_odds}",
        f"unresolved names: {len(unresolved)}",
    ]
    lines += [f"  - {src} {lg or '?'}: {name}" for src, lg, name in unresolved]
    for lg in sorted({c.league for c in counts}):
        lines += ["", f"spot check {lg}:"]
        lines += [f"  {s}" for s in spot_check(session, lg, rng)]
    lines += ["", f"ACCEPTANCE: {'PASS' if ok else 'FAIL'}"]
    return "\n".join(lines), ok
