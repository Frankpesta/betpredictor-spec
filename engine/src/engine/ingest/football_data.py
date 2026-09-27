"""football-data.co.uk results + historical odds ingest (docs/02 §1).

Facts this module relies on are recorded in `docs/discovered/football-data.md`.
`FD_COLUMN_MAP` is the only place in the codebase that names raw CSV columns.

Split: `parse_csv` is pure (bytes -> rows + counters) and unit-tested offline;
`upsert_file` writes one parsed file to the DB; `ingest_all` fetches and loops.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from engine.config import LEAGUES, LeagueDef
from engine.db.models import HistoricalOdds, League, Match, Team
from engine.db.session import session_scope
from engine.ingest.fetch import BlockedError, FetchError, PoliteClient, fresh_since_for_season
from engine.logging import get_logger

if TYPE_CHECKING:
    from engine.jobs import JobContext

log = get_logger(__name__)

SOURCE = "football_data"
FD_URL = "https://football-data.co.uk/mmz4281/{code}/{div}.csv"

# Discovered (docs/discovered/football-data.md): Date+Time are UK local time for all leagues.
FD_SOURCE_TZ = ZoneInfo("Europe/London")
# docs/02 §1.4: when `Time` is absent, assume a 15:00 UK kickoff (and log it).
FD_DEFAULT_KICKOFF = time(15, 0)
FD_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y")
# docs/02 §1.3 names the O/U columns "2.5"; that is the only O/U line the files carry.
FD_OU_LINE = 2.5


@dataclass(frozen=True)
class OddsKey:
    bookmaker: str  # B365 | PS | AVG | MAX (docs/01 historical_odds.bookmaker)
    timing: str  # open | close
    market: str  # OU | AH
    selection: str  # over | under | home | away


# The single map from our names to raw column names (docs/02 §1.3).
FD_COLUMN_MAP: dict[str, Any] = {
    "identity": {
        "div": "Div",
        "date": "Date",
        "time": "Time",
        "home": "HomeTeam",
        "away": "AwayTeam",
        "home_goals": "FTHG",
        "away_goals": "FTAG",
    },
    # Home-team handicap per timing; AH odds rows take their line from here.
    "ah_line": {"open": "AHh", "close": "AHCh"},
    "odds": {
        # O/U 2.5 opening
        OddsKey("B365", "open", "OU", "over"): "B365>2.5",
        OddsKey("B365", "open", "OU", "under"): "B365<2.5",
        OddsKey("PS", "open", "OU", "over"): "P>2.5",
        OddsKey("PS", "open", "OU", "under"): "P<2.5",
        OddsKey("AVG", "open", "OU", "over"): "Avg>2.5",
        OddsKey("AVG", "open", "OU", "under"): "Avg<2.5",
        OddsKey("MAX", "open", "OU", "over"): "Max>2.5",
        OddsKey("MAX", "open", "OU", "under"): "Max<2.5",
        # O/U 2.5 closing
        OddsKey("B365", "close", "OU", "over"): "B365C>2.5",
        OddsKey("B365", "close", "OU", "under"): "B365C<2.5",
        OddsKey("PS", "close", "OU", "over"): "PC>2.5",
        OddsKey("PS", "close", "OU", "under"): "PC<2.5",
        OddsKey("AVG", "close", "OU", "over"): "AvgC>2.5",
        OddsKey("AVG", "close", "OU", "under"): "AvgC<2.5",
        OddsKey("MAX", "close", "OU", "over"): "MaxC>2.5",
        OddsKey("MAX", "close", "OU", "under"): "MaxC<2.5",
        # AH opening (line = AHh)
        OddsKey("B365", "open", "AH", "home"): "B365AHH",
        OddsKey("B365", "open", "AH", "away"): "B365AHA",
        OddsKey("PS", "open", "AH", "home"): "PAHH",
        OddsKey("PS", "open", "AH", "away"): "PAHA",
        OddsKey("AVG", "open", "AH", "home"): "AvgAHH",
        OddsKey("AVG", "open", "AH", "away"): "AvgAHA",
        OddsKey("MAX", "open", "AH", "home"): "MaxAHH",
        OddsKey("MAX", "open", "AH", "away"): "MaxAHA",
        # AH closing (line = AHCh)
        OddsKey("B365", "close", "AH", "home"): "B365CAHH",
        OddsKey("B365", "close", "AH", "away"): "B365CAHA",
        OddsKey("PS", "close", "AH", "home"): "PCAHH",
        OddsKey("PS", "close", "AH", "away"): "PCAHA",
        OddsKey("AVG", "close", "AH", "home"): "AvgCAHH",
        OddsKey("AVG", "close", "AH", "away"): "AvgCAHA",
        OddsKey("MAX", "close", "AH", "home"): "MaxCAHH",
        OddsKey("MAX", "close", "AH", "away"): "MaxCAHA",
    },
}

_ID: dict[str, str] = FD_COLUMN_MAP["identity"]
_AH_LINE: dict[str, str] = FD_COLUMN_MAP["ah_line"]
_ODDS: dict[OddsKey, str] = FD_COLUMN_MAP["odds"]
REQUIRED_COLUMNS = ("div", "date", "home", "away", "home_goals", "away_goals")


def season_code(season: str) -> str:
    """'2024-25' -> '2425'."""
    return season[2:4] + season[5:7]


def row_hash(div: str, date_str: str, home: str, away: str) -> str:
    """docs/02 §1.4: sha256 of Div|Date|HomeTeam|AwayTeam (raw strings)."""
    return hashlib.sha256(f"{div}|{date_str}|{home}|{away}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Pure parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OddsRow:
    bookmaker: str
    timing: str
    market: str
    line: float
    selection: str
    odds: float


@dataclass(frozen=True)
class FdRow:
    div: str
    home: str
    away: str
    kickoff_utc: datetime
    home_goals: int | None
    away_goals: int | None
    status: str
    fd_row_hash: str
    odds: tuple[OddsRow, ...]


@dataclass
class FdParsed:
    rows: list[FdRow] = field(default_factory=list)
    rows_read: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    time_missing: int = 0
    odds_missing: int = 0  # odds cells that were blank / non-numeric / <= 1.0
    ah_line_missing: int = 0  # AH odds present but no line -> not stored
    missing_columns: list[str] = field(default_factory=list)


def decode(content: bytes) -> str:
    """UTF-8 (BOM stripped) with latin-1 fallback (docs/02 §1.4)."""
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1").lstrip("﻿")


def parse_odds(raw: str | None) -> float | None:
    """Decimal odds; <= 1.0 or non-numeric -> missing (docs/02 §1.4)."""
    if raw is None:
        return None
    try:
        v = float(raw.strip())
    except ValueError:
        return None
    return v if v > 1.0 else None


def parse_line(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw.strip())
    except ValueError:
        return None


def parse_goals(raw: str | None) -> int | None:
    """Goals as int; blank -> None; anything else invalid raises ValueError."""
    if raw is None or not raw.strip():
        return None
    v = float(raw.strip())
    if v < 0 or v != int(v):
        raise ValueError(f"invalid goals {raw!r}")
    return int(v)


def parse_kickoff(date_str: str, time_str: str | None) -> tuple[datetime, bool]:
    """UK-local Date (+Time) -> aware UTC datetime; returns (kickoff, time_was_missing)."""
    d: date | None = None
    for fmt in FD_DATE_FORMATS:
        try:
            d = datetime.strptime(date_str.strip(), fmt).date()  # noqa: DTZ007 - localised below
            break
        except ValueError:
            continue
    if d is None:
        raise ValueError(f"unparseable date {date_str!r}")
    if time_str and time_str.strip():
        t = datetime.strptime(time_str.strip(), "%H:%M").time()  # noqa: DTZ007 - localised below
        missing = False
    else:
        t, missing = FD_DEFAULT_KICKOFF, True
    local = datetime.combine(d, t, tzinfo=FD_SOURCE_TZ)
    return local.astimezone(UTC), missing


def parse_csv(content: bytes, expected_div: str) -> FdParsed:
    out = FdParsed()
    reader = csv.DictReader(io.StringIO(decode(content)))
    header = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = header
    cols = set(header)

    wanted = [*_ID.values(), *_AH_LINE.values(), *_ODDS.values()]
    out.missing_columns = [c for c in wanted if c not in cols]
    absent_required = [_ID[k] for k in REQUIRED_COLUMNS if _ID[k] not in cols]
    if absent_required:
        raise ValueError(f"required columns missing: {absent_required}")

    seen: set[str] = set()
    for rec in reader:
        values = {k: (v or "").strip() for k, v in rec.items() if k is not None}
        if not any(values.values()):
            out.skipped["empty_row"] += 1
            continue
        out.rows_read += 1

        div, date_str = values.get(_ID["div"], ""), values.get(_ID["date"], "")
        home, away = values.get(_ID["home"], ""), values.get(_ID["away"], "")
        if div != expected_div:
            out.skipped["wrong_div"] += 1
            continue
        if not home or not away:
            out.skipped["unresolved_team"] += 1
            continue
        try:
            kickoff, time_missing = parse_kickoff(date_str, values.get(_ID["time"]))
        except ValueError:
            out.skipped["bad_date"] += 1
            continue
        try:
            hg = parse_goals(values.get(_ID["home_goals"]))
            ag = parse_goals(values.get(_ID["away_goals"]))
        except ValueError:
            out.skipped["bad_goals"] += 1
            continue
        h = row_hash(div, date_str, home, away)
        if h in seen:
            out.skipped["duplicate_in_file"] += 1
            continue
        seen.add(h)
        out.time_missing += int(time_missing)

        odds: list[OddsRow] = []
        for key, col in _ODDS.items():
            if col not in cols:
                continue
            price = parse_odds(values.get(col))
            if price is None:
                out.odds_missing += 1
                continue
            line = (
                FD_OU_LINE if key.market == "OU" else parse_line(values.get(_AH_LINE[key.timing]))
            )
            if line is None:
                out.ah_line_missing += 1
                continue
            odds.append(OddsRow(key.bookmaker, key.timing, key.market, line, key.selection, price))

        finished = hg is not None and ag is not None
        out.rows.append(
            FdRow(
                div=div,
                home=home,
                away=away,
                kickoff_utc=kickoff,
                home_goals=hg if finished else None,
                away_goals=ag if finished else None,
                status="finished" if finished else "scheduled",
                fd_row_hash=h,
                odds=tuple(odds),
            )
        )
    return out


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------


@dataclass
class UpsertCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    odds_inserted: int = 0
    odds_updated: int = 0
    odds_unchanged: int = 0
    teams_created: int = 0


def _team_ids(
    session: Session, league_id: int, names: set[str], counts: UpsertCounts
) -> dict[str, int]:
    """football-data spelling is canonical; this ingest is the only creator of teams."""
    intl_keys = [k for k, d in LEAGUES.items() if d.international]
    intl_ids = set(session.scalars(select(League.id).where(League.key.in_(intl_keys))))
    existing: dict[str, int] = {}
    for t in session.scalars(select(Team).where(Team.canonical_name.in_(names))):
        if t.league_id in intl_ids:
            # Never let a club silently reuse a national team's row (docs/09 §3a).
            raise ValueError(
                f"football-data team {t.canonical_name!r} clashes with an INTL team; "
                "rename the INTL team before ingesting"
            )
        existing[t.canonical_name] = t.id
    for name in sorted(names - existing.keys()):
        t = Team(league_id=league_id, canonical_name=name)
        session.add(t)
        session.flush()
        existing[name] = t.id
        counts.teams_created += 1
    return existing


def upsert_file(session: Session, league: League, season: str, parsed: FdParsed) -> UpsertCounts:
    counts = UpsertCounts()
    names = {r.home for r in parsed.rows} | {r.away for r in parsed.rows}
    team_id = _team_ids(session, league.id, names, counts)

    by_hash = {
        m.fd_row_hash: m
        for m in session.scalars(
            select(Match).where(Match.fd_row_hash.in_([r.fd_row_hash for r in parsed.rows]))
        )
    }
    by_pair = {
        (m.home_team_id, m.away_team_id): m
        for m in session.scalars(
            select(Match).where(Match.league_id == league.id, Match.season == season)
        )
    }

    for r in parsed.rows:
        hid, aid = team_id[r.home], team_id[r.away]
        # Fall back to the natural key: a fixture may already exist (e.g. from SportyBet)
        # or its Date may have been corrected, which changes the hash.
        m = by_hash.get(r.fd_row_hash) or by_pair.get((hid, aid))
        new_vals = {
            "league_id": league.id,
            "season": season,
            "kickoff_utc": r.kickoff_utc,
            "home_team_id": hid,
            "away_team_id": aid,
            "home_goals": r.home_goals,
            "away_goals": r.away_goals,
            "status": r.status,
            "fd_row_hash": r.fd_row_hash,
        }
        if m is None:
            m = Match(**new_vals)
            session.add(m)
            session.flush()
            by_pair[(hid, aid)] = m
            counts.inserted += 1
        else:
            changed = False
            for k, v in new_vals.items():
                if getattr(m, k) != v:
                    setattr(m, k, v)
                    changed = True
            if changed:
                counts.updated += 1
            else:
                counts.unchanged += 1
        _upsert_odds(session, m.id, r.odds, counts)
    session.flush()
    return counts


def _odds_key(o: HistoricalOdds | OddsRow) -> tuple[str, str, str, str, float | None]:
    # O/U rows are identified by their line; an AH row's line is data (it can be
    # corrected), so AH is keyed without it and the line is updated in place.
    line = o.line if o.market == "OU" else None
    return (o.bookmaker, o.timing, o.market, o.selection, line)


def _upsert_odds(
    session: Session, match_id: int, rows: tuple[OddsRow, ...], counts: UpsertCounts
) -> None:
    existing = {
        _odds_key(o): o
        for o in session.scalars(select(HistoricalOdds).where(HistoricalOdds.match_id == match_id))
    }
    for r in rows:
        cur = existing.get(_odds_key(r))
        if cur is None:
            session.add(
                HistoricalOdds(
                    match_id=match_id,
                    bookmaker=r.bookmaker,
                    timing=r.timing,
                    market=r.market,
                    line=r.line,
                    selection=r.selection,
                    odds=r.odds,
                )
            )
            counts.odds_inserted += 1
        elif cur.line != r.line or cur.odds != r.odds:
            cur.line, cur.odds = r.line, r.odds
            counts.odds_updated += 1
        else:
            counts.odds_unchanged += 1


def assign_current_leagues(session: Session) -> None:
    """teams.league_id = league of the team's most recent match (handles promotion/relegation)."""
    session.execute(
        text(
            """
            UPDATE teams SET league_id = (
                SELECT m.league_id FROM matches m
                WHERE m.home_team_id = teams.id OR m.away_team_id = teams.id
                ORDER BY m.kickoff_utc DESC LIMIT 1
            )
            WHERE EXISTS (
                SELECT 1 FROM matches m WHERE m.home_team_id = teams.id OR m.away_team_id = teams.id
            )
            """
        )
    )


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


def ingest_all(ctx: JobContext, client: PoliteClient, leagues: list[LeagueDef]) -> None:
    """Fetch + upsert every enabled league x history season. Stops the source on 403/429."""
    today = client.today()
    report: dict[str, dict[str, Any]] = {}
    ctx.summary["football_data"] = report
    for lg in leagues:
        if lg.fd_code is None:
            continue  # INTL has its own source (ingest/international.py)
        fd_code = lg.fd_code
        for season in ctx.settings.leagues.history_seasons:
            label = f"{lg.key} {season}"
            url = FD_URL.format(code=season_code(season), div=fd_code)
            try:
                got = client.get(
                    url,
                    cache_name=f"{fd_code}_{season_code(season)}.csv",
                    fresh_since=fresh_since_for_season(season, today),
                )
            except BlockedError as exc:
                ctx.warn(f"football-data blocked us ({exc}); stopping football-data ingest")
                ctx.note("football_data_blocked", True)
                return
            except FetchError as exc:
                ctx.warn(f"football-data {label}: fetch failed: {exc}")
                report[label] = {"error": str(exc)}
                ctx.count("fd_files_failed")
                continue

            parsed = parse_csv(got.content, fd_code)
            with session_scope(ctx.db_path) as s:
                league = s.scalars(select(League).where(League.key == lg.key)).one()
                counts = upsert_file(s, league, season, parsed)
            skipped_unresolved = parsed.skipped.get("unresolved_team", 0)
            entry: dict[str, Any] = {
                "rows_read": parsed.rows_read,
                "inserted": counts.inserted,
                "updated": counts.updated,
                "unchanged": counts.unchanged,
                "skipped_empty": parsed.skipped.get("empty_row", 0),
                "skipped_unresolved_team": skipped_unresolved,
                "skipped_other": {
                    k: v
                    for k, v in parsed.skipped.items()
                    if k not in ("empty_row", "unresolved_team")
                },
                "odds_inserted": counts.odds_inserted,
                "odds_updated": counts.odds_updated,
                "odds_unchanged": counts.odds_unchanged,
                "odds_cells_missing": parsed.odds_missing,
                "ah_line_missing": parsed.ah_line_missing,
                "time_missing": parsed.time_missing,
                "teams_created": counts.teams_created,
                "missing_columns": parsed.missing_columns,
                "from_cache": got.from_cache,
            }
            report[label] = entry
            for k in ("rows_read", "inserted", "updated", "unchanged", "odds_inserted"):
                ctx.count(f"fd_{k}", entry[k])
            for reason, n in parsed.skipped.items():
                ctx.skip(f"fd_{reason}", n)
            if parsed.time_missing:
                ctx.warn(f"{label}: {parsed.time_missing} rows had no Time; assumed 15:00 UK")
            log.info(
                "football-data file ingested",
                extra={
                    "fields": {
                        "file": label,
                        **{k: v for k, v in entry.items() if isinstance(v, int)},
                    }
                },
            )

    with session_scope(ctx.db_path) as s:
        assign_current_leagues(s)
