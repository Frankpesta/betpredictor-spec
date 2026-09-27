"""Booking codes via SportyBet's share endpoint (docs/04 §3; user decision 2026-09-27:
API + manual fallback, no UI clicking — docs/discovered/sportybet/2026-09-27/booking.md).

Never logs in, never places a bet: the only calls are `factsCenter/Outcomes` (read the
current odds) and `orders/share` (create a shareable booking code).

For each slip:
  1. every leg must still be ahead of kickoff (+15 min) and within the betslip limit;
  2. current odds are read; if any leg is suspended/missing or moved more than
     `booking.max_odds_drift` → `failed`, "odds moved: …", **no booking attempted**;
  3. `orders/share` → code; the echoed ticket must contain exactly our selections.
Any failure → `booking_status = failed` + reason; the job continues with the next slip.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from engine.db.base import utcnow
from engine.db.models import Match, OddsSnapshot, Slip, Team, ValueLeg
from engine.db.session import session_scope
from engine.ingest.fetch import BlockedError, FetchError
from engine.logging import get_logger
from engine.slips.constraints import MIN_LEAD, SPORTYBET_MAX_SELECTIONS
from engine.sportybet.client import OUTCOMES, SHARE, SportyBetClient, make_transport
from engine.sportybet.markets import (
    LiveOutcome,
    SelectionKey,
    SportyBetPayloadError,
    parse_outcomes,
    share_ticket,
)

if TYPE_CHECKING:
    from engine.jobs import JobContext

log = get_logger(__name__)
_FLOAT_TOL = 1e-9


@dataclass(frozen=True)
class BookingLeg:
    event_id: str
    market_id: str
    specifier: str
    outcome_id: str
    odds_at_pick: float
    kickoff_utc: datetime
    label: str  # human-readable, used in errors and the dashboard

    @property
    def key(self) -> SelectionKey:
        return (self.event_id, self.market_id, self.specifier, self.outcome_id)

    def selection(self) -> dict[str, str]:
        return {
            "eventId": self.event_id,
            "marketId": self.market_id,
            "specifier": self.specifier,
            "outcomeId": self.outcome_id,
        }


class BookingError(RuntimeError):
    """The slip cannot be booked; the message is stored in `slips.booking_error`."""


class OddsDriftError(BookingError):
    pass


def pre_check(legs: list[BookingLeg], now: datetime) -> list[str]:
    problems = []
    if not legs:
        problems.append("slip has no legs")
    if len(legs) > SPORTYBET_MAX_SELECTIONS:
        problems.append(f"{len(legs)} legs > SportyBet limit {SPORTYBET_MAX_SELECTIONS}")
    for leg in legs:
        if leg.kickoff_utc <= now + MIN_LEAD:
            problems.append(f"starts too soon or already started: {leg.label}")
    return problems


def drift_problems(
    legs: list[BookingLeg], live: dict[SelectionKey, LiveOutcome], max_drift: float
) -> list[str]:
    """docs/04 §3.4: suspended/missing, or |now/pick − 1| > max_drift → problem."""
    problems = []
    for leg in legs:
        cur = live.get(leg.key)
        if cur is None:
            problems.append(f"no longer offered: {leg.label}")
        elif not cur.active or cur.odds is None:
            problems.append(f"suspended: {leg.label}")
        else:
            move = cur.odds / leg.odds_at_pick - 1.0
            if abs(move) > max_drift + _FLOAT_TOL:  # "more than 3%": exactly 3% is fine
                problems.append(
                    f"{leg.label}: pick {leg.odds_at_pick:.2f} now {cur.odds:.2f} ({move:+.1%})"
                )
    return problems


def book_legs(
    client: SportyBetClient, legs: list[BookingLeg], label: str, max_drift: float, now: datetime
) -> str:
    problems = pre_check(legs, now)
    if problems:
        raise BookingError("; ".join(problems))
    live = parse_outcomes(
        client.t.post_json(
            OUTCOMES,
            [{**leg.selection()} for leg in legs],
            f"booking/outcomes_{label}_{now:%H%M%S}.json",
        )
    )
    moved = drift_problems(legs, live, max_drift)
    if moved:
        raise OddsDriftError("odds moved: " + "; ".join(moved))
    payload = client.t.post_json(
        SHARE,
        {"selections": [leg.selection() for leg in legs]},
        f"booking/share_{label}_{now:%H%M%S}.json",
    )
    code, echoed = share_ticket(payload)
    wanted = {leg.key for leg in legs}
    if echoed != wanted:
        raise BookingError(
            f"booking code {code} does not contain exactly our selections "
            f"(missing {sorted(wanted - echoed)}, extra {sorted(echoed - wanted)})"
        )
    return code


# ---------------------------------------------------------------------------
# DB side
# ---------------------------------------------------------------------------


def _label(home: str, away: str, market: str, line: float, selection: str, odds: float) -> str:
    return f"{home} v {away} {market} {line:+g} {selection} @{odds:.2f}"


def slip_booking_legs(session: object, slip: Slip) -> list[BookingLeg]:
    from sqlalchemy.orm import Session

    assert isinstance(session, Session)
    legs = []
    for sl in slip.legs:
        vl = session.get_one(ValueLeg, sl.value_leg_id)
        snap = session.get_one(OddsSnapshot, vl.odds_snapshot_id)
        m = session.get_one(Match, vl.match_id)
        if not m.sportybet_event_id:
            raise BookingError(f"match {m.id} has no SportyBet event id")
        home = session.get_one(Team, m.home_team_id).canonical_name
        away = session.get_one(Team, m.away_team_id).canonical_name
        legs.append(
            BookingLeg(
                event_id=m.sportybet_event_id,
                market_id=snap.sb_market_id,
                specifier=snap.sb_specifier,
                outcome_id=snap.sb_outcome_id,
                odds_at_pick=vl.odds,
                kickoff_utc=m.kickoff_utc,
                label=_label(home, away, vl.market, vl.line, vl.selection, vl.odds),
            )
        )
    return legs


def run_book(ctx: JobContext) -> str:
    now = utcnow()
    max_drift = ctx.settings.booking.max_odds_drift
    transport = make_transport(ctx.settings)
    client = SportyBetClient(transport)
    lines: list[str] = []
    counts = {"booked": 0, "failed": 0}
    try:
        with session_scope(ctx.db_path) as s:
            query = select(Slip.id).where(Slip.booking_status == "pending", Slip.status == "open")
            only = ctx.params.get("slip_id")
            if only is not None:  # API rebook: just this slip
                query = query.where(Slip.id == int(only))
            slip_ids = list(s.scalars(query.order_by(Slip.id)))
        for slip_id in slip_ids:
            with session_scope(ctx.db_path) as s:
                slip = s.get_one(Slip, slip_id)
                try:
                    legs = slip_booking_legs(s, slip)
                    code = book_legs(client, legs, f"slip{slip.id}", max_drift, now)
                except BlockedError as exc:
                    slip.booking_status, slip.booking_error = "failed", f"blocked: {exc}"
                    counts["failed"] += 1
                    ctx.warn(f"SportyBet blocked us ({exc}); stopping `make book`")
                    lines.append(f"slip #{slip.id} {slip.pool}/{slip.slip_type}: FAILED blocked")
                    break
                except (BookingError, FetchError, SportyBetPayloadError) as exc:
                    slip.booking_status, slip.booking_error = "failed", str(exc)
                    counts["failed"] += 1
                    log.warning(
                        "booking failed", extra={"fields": {"slip": slip.id, "error": str(exc)}}
                    )
                    lines.append(f"slip #{slip.id} {slip.pool}/{slip.slip_type}: FAILED {exc}")
                    continue
                slip.booking_status, slip.booking_code, slip.booking_error = "booked", code, None
                counts["booked"] += 1
                lines.append(f"slip #{slip.id} {slip.pool}/{slip.slip_type}: booked {code}")
    finally:
        ctx.count("http_requests", getattr(transport, "requests_made", 0))
        transport.close()
    ctx.note("booked", counts["booked"])
    ctx.note("failed", counts["failed"])
    return "\n".join(lines) if lines else "no pending slips to book"


def run_book_test(ctx: JobContext) -> str:
    """Acceptance helper (docs/08 Phase 5): book a 2-leg test selection from the latest
    odds — two different upcoming matches, main O/U 2.5 — through the exact same
    `book_legs` path. Writes nothing to `slips`; the code is printed and noted."""
    now = utcnow()
    with session_scope(ctx.db_path) as s:
        rows = s.execute(
            select(Match, OddsSnapshot)
            .join(OddsSnapshot, OddsSnapshot.match_id == Match.id)
            .where(
                Match.status == "scheduled",
                Match.sportybet_event_id.is_not(None),
                Match.kickoff_utc > now + MIN_LEAD,
                OddsSnapshot.market == "OU",
                OddsSnapshot.line == 2.5,
                OddsSnapshot.selection == "over",
            )
            .order_by(Match.kickoff_utc, Match.id, OddsSnapshot.captured_at.desc())
        ).all()
        chosen: dict[int, tuple[Match, OddsSnapshot]] = {}
        for m, snap in rows:
            chosen.setdefault(m.id, (m, snap))
        picks = list(chosen.values())[:2]
        if len(picks) < 2:
            raise BookingError("need two upcoming matches with O/U 2.5 odds; run `make odds`")
        legs = []
        for m, snap in picks:
            home = s.get_one(Team, m.home_team_id).canonical_name
            away = s.get_one(Team, m.away_team_id).canonical_name
            assert m.sportybet_event_id is not None
            legs.append(
                BookingLeg(
                    event_id=m.sportybet_event_id,
                    market_id=snap.sb_market_id,
                    specifier=snap.sb_specifier,
                    outcome_id=snap.sb_outcome_id,
                    odds_at_pick=snap.odds,
                    kickoff_utc=m.kickoff_utc,
                    label=_label(home, away, "OU", 2.5, "over", snap.odds),
                )
            )
    transport = make_transport(ctx.settings)
    try:
        code = book_legs(
            SportyBetClient(transport), legs, "test", ctx.settings.booking.max_odds_drift, now
        )
    finally:
        transport.close()
    ctx.note("test_booking_code", code)
    ctx.note("test_legs", [leg.label for leg in legs])
    return f"TEST booking code: {code}\n" + "\n".join(f"  leg: {leg.label}" for leg in legs)
