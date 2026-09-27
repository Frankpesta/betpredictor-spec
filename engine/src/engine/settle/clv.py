"""Closing-odds snapshot and CLV (docs/06 §1).

`make close` (run ~30-60 min before the main kickoffs) fetches the current SportyBet
lines — one `factsCenter/event` request per match — only for matches in open slips
that kick off within CLOSE_WINDOW, stores them as snapshot_kind 'close', then sets
`closing_odds` / `clv` on every slip leg that has a close snapshot of the same market,
line and selection. If that exact line is no longer offered, CLV stays NULL.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.db.base import utcnow
from engine.db.models import Match, OddsSnapshot, Slip, SlipLeg, ValueLeg
from engine.db.session import session_scope
from engine.ingest.fetch import BlockedError, FetchError
from engine.logging import get_logger
from engine.sportybet.client import SportyBetClient, make_transport
from engine.sportybet.markets import SportyBetPayloadError

if TYPE_CHECKING:
    from engine.jobs import JobContext

log = get_logger(__name__)

CLOSE_WINDOW = timedelta(minutes=90)  # docs/06 §1
CLOSE_KIND = "close"


def clv(odds_taken: float, closing_odds: float) -> float:
    """docs/06 §1: odds_taken / closing_odds − 1 (positive = beat the closing price)."""
    if odds_taken <= 1.0 or closing_odds <= 1.0:
        raise ValueError("decimal odds must be > 1")
    return odds_taken / closing_odds - 1.0


def update_leg_clv(session: Session) -> int:
    """Fill closing_odds/clv from the latest pre-kickoff close snapshot; returns legs updated."""
    updated = 0
    rows = session.execute(
        select(SlipLeg, ValueLeg, Match)
        .join(ValueLeg, ValueLeg.id == SlipLeg.value_leg_id)
        .join(Match, Match.id == ValueLeg.match_id)
    ).all()
    for sl, vl, m in rows:
        close = session.scalars(
            select(OddsSnapshot)
            .where(
                OddsSnapshot.match_id == m.id,
                OddsSnapshot.snapshot_kind == CLOSE_KIND,
                OddsSnapshot.market == vl.market,
                OddsSnapshot.line == vl.line,
                OddsSnapshot.selection == vl.selection,
                OddsSnapshot.captured_at <= m.kickoff_utc,
            )
            .order_by(OddsSnapshot.captured_at.desc(), OddsSnapshot.id.desc())
            .limit(1)
        ).one_or_none()
        if close is None:
            continue
        value = clv(vl.odds, close.odds)
        if sl.closing_odds != close.odds or sl.clv != value:
            sl.closing_odds, sl.clv = close.odds, value
            updated += 1
    return updated


def run_close(ctx: JobContext) -> str:
    now = utcnow()
    with session_scope(ctx.db_path) as s:
        targets = [
            (m.id, m.sportybet_event_id)
            for m in s.scalars(
                select(Match)
                .join(ValueLeg, ValueLeg.match_id == Match.id)
                .join(SlipLeg, SlipLeg.value_leg_id == ValueLeg.id)
                .join(Slip, Slip.id == SlipLeg.slip_id)
                .where(
                    Slip.status == "open",
                    Match.kickoff_utc > now,
                    Match.kickoff_utc <= now + CLOSE_WINDOW,
                    Match.sportybet_event_id.is_not(None),
                )
                .distinct()
            )
        ]
    stored = 0
    transport = make_transport(ctx.settings)
    client = SportyBetClient(transport)
    try:
        for match_id, event_id in targets:
            assert event_id is not None
            try:
                parsed = client.event_detail(event_id)
            except BlockedError as exc:
                ctx.warn(f"SportyBet blocked us ({exc}); stopping `make close`")
                break
            except (FetchError, SportyBetPayloadError) as exc:
                ctx.warn(f"close snapshot failed for match {match_id}: {exc}")
                continue
            with session_scope(ctx.db_path) as s:
                for ev in parsed.events:
                    for o in ev.odds:
                        s.add(
                            OddsSnapshot(
                                match_id=match_id,
                                captured_at=now,
                                snapshot_kind=CLOSE_KIND,
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
                        stored += 1
    finally:
        ctx.count("http_requests", getattr(transport, "requests_made", 0))
        transport.close()
    with session_scope(ctx.db_path) as s:
        updated = update_leg_clv(s)
    ctx.note("matches", len(targets))
    ctx.note("close_snapshots", stored)
    ctx.note("legs_clv_updated", updated)
    return (
        f"matches in open slips kicking off within 90 min: {len(targets)}; "
        f"close snapshots stored: {stored}; legs with CLV updated: {updated}"
    )
