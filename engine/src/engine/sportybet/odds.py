"""`make odds`: upcoming fixtures + AH/OU odds from SportyBet (docs/04 §2.2-2.3).

One `pcEvents` request per competition (all its tournaments, markets 16 + 18).
Events kicking off within `general.horizon_hours` are resolved to canonical teams (exact
aliases, source `sportybet`), upserted into `matches` with `sportybet_event_id`, and
every normalised line is stored in `odds_snapshots` (snapshot_kind 'pick').
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import LeagueDef
from engine.db.base import utcnow
from engine.db.models import League, Match, OddsSnapshot
from engine.db.session import session_scope
from engine.ingest.fetch import BlockedError, FetchError
from engine.ingest.international import DATE_TOLERANCE
from engine.logging import get_logger
from engine.mapping.teams import Resolver
from engine.sportybet.client import SportyBetClient, make_transport
from engine.sportybet.markets import SbEvent, SportyBetPayloadError

if TYPE_CHECKING:
    from engine.jobs import JobContext

log = get_logger(__name__)

SNAPSHOT_KIND = "pick"


def season_of(kickoff: datetime) -> str:
    y = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


@dataclass
class OddsCounts:
    events_seen: int = 0
    events_stored: int = 0
    matches_inserted: int = 0
    matches_updated: int = 0
    snapshots: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    unresolved: set[str] = field(default_factory=set)


def _find_match(
    session: Session, league: League, lg: LeagueDef, ev: SbEvent, hid: int, aid: int
) -> Match | None:
    by_event = session.scalars(
        select(Match).where(Match.sportybet_event_id == ev.event_id)
    ).one_or_none()
    if by_event is not None:
        return by_event
    candidates = session.scalars(
        select(Match).where(
            Match.league_id == league.id, Match.home_team_id == hid, Match.away_team_id == aid
        )
    ).all()
    if lg.international:
        # National teams can meet several times a season: match by date (docs/09 §3a).
        return next(
            (m for m in candidates if abs(m.kickoff_utc - ev.kickoff_utc) <= DATE_TOLERANCE),
            None,
        )
    season = season_of(ev.kickoff_utc)
    return next((m for m in candidates if m.season == season and m.fixture_key == ""), None)


def store_event(
    session: Session, league: League, lg: LeagueDef, ev: SbEvent, resolver: Resolver,
    captured_at: datetime, counts: OddsCounts,
) -> None:  # fmt: skip
    hid = resolver.resolve(ev.home, lg.key)
    aid = resolver.resolve(ev.away, lg.key)
    if hid is None or aid is None:
        counts.skipped["unresolved_team"] += 1
        counts.unresolved.update(n for n, i in ((ev.home, hid), (ev.away, aid)) if i is None)
        return
    if not ev.odds:
        counts.skipped["no_usable_odds"] += 1
        return
    match = _find_match(session, league, lg, ev, hid, aid)
    if match is None:
        match = Match(
            league_id=league.id,
            season=season_of(ev.kickoff_utc),
            kickoff_utc=ev.kickoff_utc,
            home_team_id=hid,
            away_team_id=aid,
            status="scheduled",
            fixture_key=ev.kickoff_utc.date().isoformat() if lg.international else "",
            sportybet_event_id=ev.event_id,
        )
        session.add(match)
        counts.matches_inserted += 1
    else:
        if match.status == "finished":
            counts.skipped["already_finished"] += 1
            return
        if match.sportybet_event_id not in (None, ev.event_id):
            log.warning(
                "match already linked to another SportyBet event; not relinking",
                extra={
                    "fields": {
                        "match_id": match.id,
                        "have": match.sportybet_event_id,
                        "got": ev.event_id,
                    }
                },
            )
            counts.skipped["event_id_conflict"] += 1
            return
        changed = False
        if match.sportybet_event_id is None:
            match.sportybet_event_id, changed = ev.event_id, True
        if match.kickoff_utc != ev.kickoff_utc:
            match.kickoff_utc, changed = ev.kickoff_utc, True
        counts.matches_updated += int(changed)
    session.flush()
    for o in ev.odds:
        session.add(
            OddsSnapshot(
                match_id=match.id,
                captured_at=captured_at,
                snapshot_kind=SNAPSHOT_KIND,
                market=o.market,
                line=o.line,
                selection=o.selection,
                odds=o.odds,
                sb_market_id=o.sb_market_id,
                sb_specifier=o.sb_specifier,
                sb_outcome_id=o.sb_outcome_id,
                is_active=True,
            )
        )
    counts.snapshots += len(ev.odds)
    counts.events_stored += 1


def run_odds(ctx: JobContext, transport_kind: str = "httpx") -> str:
    now = utcnow()
    horizon = now + timedelta(hours=ctx.settings.general.horizon_hours)  # docs/04 §2.2
    transport = make_transport(ctx.settings, transport_kind)
    client = SportyBetClient(transport)
    report: dict[str, dict[str, object]] = {}
    lines: list[str] = []
    try:
        for lg in ctx.settings.enabled_leagues():
            if not lg.sb_tournaments:
                continue
            try:
                parsed = client.upcoming_events(lg.key, lg.sb_tournaments)
            except BlockedError as exc:
                ctx.warn(f"SportyBet blocked us ({exc}); stopping `make odds`")
                ctx.note("sportybet_blocked", True)
                break
            except (FetchError, SportyBetPayloadError) as exc:
                ctx.warn(f"{lg.key}: SportyBet listing failed: {exc}")
                report[lg.key] = {"error": str(exc)}
                continue
            counts = OddsCounts(skipped=Counter(parsed.skipped))
            with session_scope(ctx.db_path) as s:
                league = s.scalars(select(League).where(League.key == lg.key)).one()
                resolver = Resolver(s, "sportybet")
                for ev in parsed.events:
                    counts.events_seen += 1
                    if not (now < ev.kickoff_utc <= horizon):
                        # Still resolve names so `make map-teams` sees every spelling
                        # before the fixture enters the window; nothing is stored.
                        for name in (ev.home, ev.away):
                            if resolver.resolve(name, lg.key) is None:
                                counts.unresolved.add(name)
                        counts.skipped["outside_horizon"] += 1
                        continue
                    store_event(s, league, lg, ev, resolver, now, counts)
                resolver.flush_misses(now)
            report[lg.key] = {
                "events_seen": counts.events_seen,
                "events_stored": counts.events_stored,
                "matches_inserted": counts.matches_inserted,
                "matches_updated": counts.matches_updated,
                "snapshots": counts.snapshots,
                "skipped": dict(counts.skipped),
                "unresolved": sorted(counts.unresolved),
            }
            ctx.count("events_stored", counts.events_stored)
            ctx.count("snapshots", counts.snapshots)
            for reason, n in counts.skipped.items():
                ctx.skip(reason, n)
            lines.append(
                f"{lg.key:<7} seen {counts.events_seen:>3}  stored {counts.events_stored:>3}  "
                f"new matches {counts.matches_inserted:>3}  snapshots {counts.snapshots:>5}  "
                f"unresolved {len(counts.unresolved)}"
            )
            if counts.unresolved:
                lines.append("        unresolved: " + ", ".join(sorted(counts.unresolved)))
    finally:
        ctx.count("http_requests", getattr(transport, "requests_made", 0))
        transport.close()
    ctx.summary["odds"] = report
    return "\n".join(lines) if lines else "no competitions fetched"
