"""Understat xG ingest — top five leagues only (docs/02 §2).

Facts this module relies on are recorded in `docs/discovered/understat.md`:
match data comes from `GET /getLeagueData/{league}/{season_start_year}` (JSON),
and match `datetime` values are UTC.

Understat never creates matches or teams. Its rows are joined to football-data
matches on (league, season, resolved home, resolved away); football-data goals win
on disagreement (the mismatch is counted and logged). If Understat is unreachable
the ingest carries on without xG (docs/02 §2.4).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import LeagueDef
from engine.db.models import League, Match
from engine.db.session import session_scope
from engine.ingest.fetch import (
    BlockedError,
    FetchError,
    PoliteClient,
    fresh_since_for_season,
    season_start_year,
)
from engine.logging import get_logger
from engine.mapping.teams import Resolver

if TYPE_CHECKING:
    from engine.jobs import JobContext

log = get_logger(__name__)

SOURCE = "understat"
BASE_URL = "https://understat.com"
LEAGUE_DATA_URL = BASE_URL + "/getLeagueData/{league}/{year}"
LEAGUE_PAGE_URL = BASE_URL + "/league/{league}/{year}"
# docs/02 §2.3 acceptance: xG join rate per league-season must be at least this.
MIN_XG_JOIN_RATE = 0.98


@dataclass(frozen=True)
class UnderstatMatch:
    understat_id: str
    kickoff_utc: datetime
    home: str
    away: str
    home_goals: int | None
    away_goals: int | None
    home_xg: float | None
    away_xg: float | None
    is_result: bool


@dataclass
class UnderstatParsed:
    matches: list[UnderstatMatch] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)


def _num(v: Any, cast: type[int] | type[float]) -> int | float | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return cast(float(v)) if cast is int else float(v)


def parse_league_data(content: bytes) -> UnderstatParsed:
    """Parse a getLeagueData JSON response into matches (pure)."""
    data = json.loads(content.decode("utf-8"))
    dates = data.get("dates") if isinstance(data, dict) else None
    if not isinstance(dates, list):
        raise ValueError("getLeagueData response has no 'dates' list")
    out = UnderstatParsed()
    for d in dates:
        try:
            is_result = bool(d["isResult"])
            hg, ag = _num(d["goals"]["h"], int), _num(d["goals"]["a"], int)
            hx, ax = _num(d["xG"]["h"], float), _num(d["xG"]["a"], float)
            kickoff = datetime.strptime(d["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            m = UnderstatMatch(
                understat_id=str(d["id"]),
                kickoff_utc=kickoff,
                home=str(d["h"]["title"]),
                away=str(d["a"]["title"]),
                home_goals=None if hg is None else int(hg),
                away_goals=None if ag is None else int(ag),
                home_xg=hx,
                away_xg=ax,
                is_result=is_result,
            )
        except (KeyError, TypeError, ValueError):
            out.skipped["malformed_match"] += 1
            continue
        if m.is_result and (m.home_xg is None or m.away_xg is None):
            out.skipped["result_without_xg"] += 1
            continue
        out.matches.append(m)
    return out


@dataclass
class JoinResult:
    understat_results: int = 0
    joined: int = 0
    xg_set: int = 0
    xg_unchanged: int = 0
    unresolved_team: int = 0
    no_fd_match: int = 0
    goal_mismatch: int = 0
    kickoff_diff: Counter[str] = field(default_factory=Counter)
    fd_finished: int = 0
    fd_finished_with_xg: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def join_rate(self) -> float | None:
        return self.fd_finished_with_xg / self.fd_finished if self.fd_finished else None


_MAX_EXAMPLES = 5


def _kickoff_bucket(fd: datetime, us: datetime) -> str:
    minutes = round(abs((fd - us).total_seconds()) / 60)
    if minutes == 0:
        return "same"
    if minutes <= 60:
        return "<=1h"
    if minutes < 24 * 60:
        return "<1d"
    return ">=1d"


def join_xg(
    session: Session, league: League, season: str, parsed: UnderstatParsed, resolver: Resolver
) -> JoinResult:
    res = JoinResult()
    by_pair = {
        (m.home_team_id, m.away_team_id): m
        for m in session.scalars(
            select(Match).where(Match.league_id == league.id, Match.season == season)
        )
    }
    for um in parsed.matches:
        hid = resolver.resolve(um.home, league.key)
        aid = resolver.resolve(um.away, league.key)
        if not um.is_result:
            continue
        res.understat_results += 1
        if hid is None or aid is None:
            res.unresolved_team += 1
            continue
        m = by_pair.get((hid, aid))
        if m is None:
            res.no_fd_match += 1
            if len(res.examples) < _MAX_EXAMPLES:
                res.examples.append(f"no fd match: {um.home} v {um.away} {um.kickoff_utc:%Y-%m-%d}")
            continue
        res.joined += 1
        res.kickoff_diff[_kickoff_bucket(m.kickoff_utc, um.kickoff_utc)] += 1
        if m.status == "finished" and (m.home_goals, m.away_goals) != (
            um.home_goals,
            um.away_goals,
        ):
            res.goal_mismatch += 1
            log.warning(
                "understat goals disagree with football-data; keeping football-data",
                extra={
                    "fields": {
                        "match_id": m.id,
                        "fd": f"{m.home_goals}-{m.away_goals}",
                        "understat": f"{um.home_goals}-{um.away_goals}",
                        "understat_id": um.understat_id,
                    }
                },
            )
        if (m.home_xg, m.away_xg) == (um.home_xg, um.away_xg):
            res.xg_unchanged += 1
        else:
            m.home_xg, m.away_xg = um.home_xg, um.away_xg
            res.xg_set += 1
    session.flush()

    finished = [m for m in by_pair.values() if m.status == "finished"]
    res.fd_finished = len(finished)
    res.fd_finished_with_xg = sum(1 for m in finished if m.home_xg is not None)
    return res


def ingest_all(ctx: JobContext, client: PoliteClient, leagues: list[LeagueDef]) -> None:
    today = client.today()
    report: dict[str, dict[str, Any]] = {}
    ctx.summary["understat"] = report
    for lg in leagues:
        if lg.understat_key is None:
            continue
        for season in ctx.settings.leagues.history_seasons:
            label = f"{lg.key} {season}"
            year = season_start_year(season)
            try:
                got = client.get(
                    LEAGUE_DATA_URL.format(league=lg.understat_key, year=year),
                    cache_name=f"{lg.understat_key}_{year}.json",
                    fresh_since=fresh_since_for_season(season, today),
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": LEAGUE_PAGE_URL.format(league=lg.understat_key, year=year),
                    },
                )
            except BlockedError as exc:
                ctx.warn(
                    f"understat blocked us ({exc}); stopping Understat — "
                    "affected leagues fall back to the goals-only model"
                )
                ctx.note("understat_blocked", True)
                return
            except FetchError as exc:
                ctx.warn(f"understat {label}: unreachable ({exc}); no xG for it this run")
                report[label] = {"error": str(exc)}
                ctx.count("understat_files_failed")
                continue

            try:
                parsed = parse_league_data(got.content)
            except ValueError as exc:
                ctx.warn(f"understat {label}: unparseable response ({exc}); saved at {got.path}")
                report[label] = {"error": f"parse: {exc}"}
                ctx.count("understat_files_failed")
                continue

            with session_scope(ctx.db_path) as s:
                league = s.scalars(select(League).where(League.key == lg.key)).one()
                resolver = Resolver(s, SOURCE)
                jr = join_xg(s, league, season, parsed, resolver)
                unresolved_names = sorted({name for name, _ in resolver.misses})
                resolver.flush_misses()

            rate = jr.join_rate
            entry: dict[str, Any] = {
                "understat_matches": len(parsed.matches),
                "understat_results": jr.understat_results,
                "joined": jr.joined,
                "xg_set": jr.xg_set,
                "xg_unchanged": jr.xg_unchanged,
                "skipped_unresolved_team": jr.unresolved_team,
                "skipped_no_fd_match": jr.no_fd_match,
                "skipped_parse": dict(parsed.skipped),
                "goal_mismatch": jr.goal_mismatch,
                "kickoff_diff": dict(jr.kickoff_diff),
                "fd_finished": jr.fd_finished,
                "fd_finished_with_xg": jr.fd_finished_with_xg,
                "join_rate": None if rate is None else round(rate, 4),
                "unresolved_names": unresolved_names,
                "examples": jr.examples,
                "from_cache": got.from_cache,
            }
            report[label] = entry
            ctx.count("understat_xg_set", jr.xg_set)
            ctx.skip("understat_unresolved_team", jr.unresolved_team)
            ctx.skip("understat_no_fd_match", jr.no_fd_match)
            for reason, n in parsed.skipped.items():
                ctx.skip(f"understat_{reason}", n)
            if jr.goal_mismatch:
                ctx.warn(f"{label}: {jr.goal_mismatch} Understat/football-data goal mismatches")
            if rate is not None and rate < MIN_XG_JOIN_RATE:
                ctx.warn(f"{label}: xG join rate {rate:.1%} < {MIN_XG_JOIN_RATE:.0%}")
            log.info(
                "understat season joined",
                extra={
                    "fields": {
                        "file": label,
                        **{k: v for k, v in entry.items() if isinstance(v, int | float)},
                    }
                },
            )
