"""International men's A results (docs/09; facts in docs/discovered/international-results.md).

Source: martj42/international_results `results.csv` — results only, local dates, no
kickoff times, a `neutral` flag. This ingest is the only creator of INTL teams
(the dataset spelling is canonical for national teams), mirroring football-data for clubs.
"""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.db.models import League, Match, Team
from engine.db.session import session_scope
from engine.ingest.fetch import BlockedError, FetchError, PoliteClient, season_start_year
from engine.logging import get_logger

if TYPE_CHECKING:
    from engine.jobs import JobContext

log = get_logger(__name__)

SOURCE = "international_results"
LEAGUE_KEY = "INTL"
RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
EXPECTED_HEADER = (
    "date",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "tournament",
    "city",
    "country",
    "neutral",
)
# The dataset has no kickoff times (discovered): results are stored at noon UTC.
DEFAULT_KICKOFF = time(12, 0)
# Local vs UTC dates can differ by one day when matching SportyBet fixtures.
DATE_TOLERANCE = timedelta(days=1)
CLASH_SUFFIX = " (national team)"
# "A internationals" are between FIFA members. The dataset has no membership flag, so a
# team counts as a FIFA side if it played a World Cup qualifier inside the window —
# this yields exactly FIFA's 211 members (docs/discovered/international-results.md).
FIFA_MARKER_TOURNAMENT = "FIFA World Cup qualification"
_NEUTRAL = {"TRUE": True, "FALSE": False}


@dataclass(frozen=True)
class IntlRow:
    day: date
    home: str
    away: str
    home_goals: int
    away_goals: int
    tournament: str
    neutral: bool

    @property
    def kickoff_utc(self) -> datetime:
        return datetime.combine(self.day, DEFAULT_KICKOFF, tzinfo=UTC)


@dataclass
class IntlParsed:
    rows: list[IntlRow] = field(default_factory=list)
    rows_read: int = 0
    before_window: int = 0
    skipped: Counter[str] = field(default_factory=Counter)


def fifa_sides(rows: list[IntlRow]) -> set[str]:
    return {t for r in rows if r.tournament == FIFA_MARKER_TOURNAMENT for t in (r.home, r.away)}


def keep_fifa_only(parsed: IntlParsed) -> set[str]:
    """Drop (and count) matches involving a non-FIFA side; returns the FIFA set."""
    fifa = fifa_sides(parsed.rows)
    kept = [r for r in parsed.rows if r.home in fifa and r.away in fifa]
    dropped = len(parsed.rows) - len(kept)
    if dropped:
        parsed.skipped["non_fifa_team"] += dropped
    parsed.rows = kept
    return fifa


def season_of(day: date) -> str:
    """Jul-Jun convention shared with the club leagues: 2026-06-15 -> '2025-26'."""
    y = day.year if day.month >= 7 else day.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def parse_results(content: bytes, since: date) -> IntlParsed:
    out = IntlParsed()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    header = tuple(reader.fieldnames or ())
    missing = [c for c in EXPECTED_HEADER if c not in header]
    if missing:
        raise ValueError(f"results.csv columns missing: {missing}")
    seen: set[tuple[date, str, str]] = set()
    for rec in reader:
        out.rows_read += 1
        try:
            day = date.fromisoformat(rec["date"].strip())
        except ValueError:
            out.skipped["bad_date"] += 1
            continue
        if day < since:
            out.before_window += 1
            continue
        home, away = rec["home_team"].strip(), rec["away_team"].strip()
        if not home or not away:
            out.skipped["missing_team"] += 1
            continue
        try:
            hg, ag = int(rec["home_score"]), int(rec["away_score"])
        except ValueError:
            out.skipped["no_score"] += 1
            continue
        neutral = _NEUTRAL.get(rec["neutral"].strip().upper())
        if neutral is None:
            out.skipped["bad_neutral"] += 1
            continue
        if (day, home, away) in seen:
            out.skipped["duplicate_same_day"] += 1
            continue
        seen.add((day, home, away))
        out.rows.append(IntlRow(day, home, away, hg, ag, rec["tournament"].strip(), neutral))
    return out


@dataclass
class IntlCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    teams_created: int = 0
    renamed_clashes: list[str] = field(default_factory=list)


def _team_ids(
    session: Session, league: League, names: set[str], counts: IntlCounts
) -> dict[str, int]:
    """Dataset name -> team id; a name already used by a club gets CLASH_SUFFIX."""
    all_teams = {t.canonical_name: t for t in session.scalars(select(Team))}
    out: dict[str, int] = {}
    for name in sorted(names):
        canonical = name
        existing = all_teams.get(name)
        if existing is not None and existing.league_id != league.id:
            canonical = name + CLASH_SUFFIX
            if name not in counts.renamed_clashes:
                counts.renamed_clashes.append(name)
        team = all_teams.get(canonical)
        if team is None:
            team = Team(league_id=league.id, canonical_name=canonical)
            session.add(team)
            session.flush()
            all_teams[canonical] = team
            counts.teams_created += 1
        out[name] = team.id
    return out


def upsert_results(session: Session, league: League, parsed: IntlParsed) -> IntlCounts:
    counts = IntlCounts()
    ids = _team_ids(
        session, league, {r.home for r in parsed.rows} | {r.away for r in parsed.rows}, counts
    )
    existing: dict[tuple[int, int], list[Match]] = defaultdict(list)
    for m in session.scalars(select(Match).where(Match.league_id == league.id)):
        existing[(m.home_team_id, m.away_team_id)].append(m)

    for r in parsed.rows:
        hid, aid = ids[r.home], ids[r.away]
        candidates = existing[(hid, aid)]
        key = r.day.isoformat()
        # Exact fixture first; the ±1-day window is only for SportyBet-created fixtures
        # (exact UTC kickoff vs local date). Teams can play on consecutive days, so a
        # dataset row must never be matched to another dataset row by proximity.
        match = next((m for m in candidates if m.fixture_key == key), None) or next(
            (
                m
                for m in candidates
                if m.sportybet_event_id is not None
                and abs(m.kickoff_utc.date() - r.day) <= DATE_TOLERANCE
            ),
            None,
        )
        vals: dict[str, Any] = {
            "home_goals": r.home_goals,
            "away_goals": r.away_goals,
            "status": "finished",
            "neutral": r.neutral,
        }
        if match is None:
            match = Match(
                league_id=league.id,
                season=season_of(r.day),
                kickoff_utc=r.kickoff_utc,
                home_team_id=hid,
                away_team_id=aid,
                fixture_key=r.day.isoformat(),
                **vals,
            )
            session.add(match)
            existing[(hid, aid)].append(match)
            counts.inserted += 1
            continue
        changed = False
        for k, v in vals.items():
            if getattr(match, k) != v:
                setattr(match, k, v)
                changed = True
        # A SportyBet-created fixture keeps its exact kickoff; others follow the dataset.
        if match.sportybet_event_id is None and match.kickoff_utc != r.kickoff_utc:
            match.kickoff_utc = r.kickoff_utc
            changed = True
        counts.updated += int(changed)
        counts.unchanged += int(not changed)
    session.flush()
    return counts


def remove_non_fifa(session: Session, league: League, fifa: set[str]) -> int:
    """Delete stored INTL results involving non-FIFA sides (never SportyBet fixtures)."""
    names = {t.id: t.canonical_name for t in session.scalars(select(Team))}
    doomed = [
        m
        for m in session.scalars(select(Match).where(Match.league_id == league.id))
        if m.sportybet_event_id is None
        and (
            names[m.home_team_id].removesuffix(CLASH_SUFFIX) not in fifa
            or names[m.away_team_id].removesuffix(CLASH_SUFFIX) not in fifa
        )
    ]
    for m in doomed:
        session.delete(m)
    session.flush()
    return len(doomed)


def window_start(ctx: JobContext) -> date:
    """Same history as the club leagues: from 1 July of the first history season."""
    return date(season_start_year(ctx.settings.leagues.history_seasons[0]), 7, 1)


def ingest(ctx: JobContext, client: PoliteClient) -> None:
    try:
        got = client.get(RESULTS_URL, cache_name="results.csv", fresh_since=client.today())
    except BlockedError as exc:
        ctx.warn(f"international results blocked ({exc}); INTL not updated this run")
        return
    except FetchError as exc:
        ctx.warn(f"international results unreachable ({exc}); INTL not updated this run")
        return
    parsed = parse_results(got.content, window_start(ctx))
    fifa = keep_fifa_only(parsed)
    with session_scope(ctx.db_path) as s:
        league = s.scalars(select(League).where(League.key == LEAGUE_KEY)).one()
        removed = remove_non_fifa(s, league, fifa)
        counts = upsert_results(s, league, parsed)
    if removed:
        ctx.warn(f"removed {removed} stored INTL matches involving non-FIFA sides")
    latest = max((r.day for r in parsed.rows), default=None)
    ctx.summary["international"] = {
        "rows_read": parsed.rows_read,
        "before_window": parsed.before_window,
        "in_window": len(parsed.rows),
        "inserted": counts.inserted,
        "updated": counts.updated,
        "unchanged": counts.unchanged,
        "teams_created": counts.teams_created,
        "skipped": dict(parsed.skipped),
        "renamed_clashes": counts.renamed_clashes,
        "fifa_sides": len(fifa),
        "removed_non_fifa_matches": removed,
        "latest_result": None if latest is None else latest.isoformat(),
        "from_cache": got.from_cache,
    }
    for reason, n in parsed.skipped.items():
        ctx.skip(f"intl_{reason}", n)
    for name in counts.renamed_clashes:
        ctx.warn(f"INTL team {name!r} clashes with a club name; stored as {name + CLASH_SUFFIX!r}")
    log.info("international results ingested", extra={"fields": ctx.summary["international"]})
