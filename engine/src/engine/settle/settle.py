"""Leg and slip settlement (docs/06 §2-3). SportyBet rules verified in
docs/discovered/sportybet/2026-09-27/settlement-rules.md: a void leg is settled on the
remaining selections (multiplier 1, rule 4.4); any LOST leg loses the slip (rule 4.11).

Pure part: `settle_leg`, `slip_outcome`. Job part: `run_settle` gets 90-minute scores
from SportyBet (primary) and from `matches` (football-data / international dataset,
secondary). If both exist and disagree the match becomes `needs_review` and is never
settled; a leg is voided only when the match status says postponed/cancelled/abandoned.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

from sqlalchemy import select

from engine.config import LEAGUES
from engine.db.base import utcnow
from engine.db.models import League, Match, Slip, SlipLeg, ValueLeg
from engine.db.session import session_scope
from engine.ingest.fetch import BlockedError, FetchError
from engine.logging import get_logger
from engine.model.markets import (
    Market,
    Result,
    Selection,
    result_multiplier,
    settle,
    split_period,
)
from engine.sportybet.client import SportyBetClient, make_transport
from engine.sportybet.markets import SportyBetPayloadError
from engine.sportybet.results import PAGE_SIZE, RESULTS_PATH, SbResult, day_window_ms, parse_results

if TYPE_CHECKING:
    from engine.jobs import JobContext

log = get_logger(__name__)

SETTLE_AFTER = timedelta(hours=2.5)  # docs/06 §2
VOID_MATCH_STATUSES = frozenset({"postponed", "cancelled", "abandoned"})
_TOL = 1e-12


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


def settle_leg(
    market: str, line: float, selection: str, odds: float, score: tuple[int, int] | None
) -> tuple[str, float]:
    """→ (result, result_multiplier). `score=None` means the match is void."""
    if score is None:
        return "void", 1.0
    result: Result = settle(cast(Market, market), cast(Selection, selection), line, *score)
    return result, result_multiplier(result, odds)


@dataclass(frozen=True)
class SlipOutcome:
    status: str  # open | won | lost | void | partial
    return_multiplier: float | None


def slip_outcome(legs: Sequence[tuple[str, float | None]]) -> SlipOutcome:
    """legs: (result, multiplier) with result 'pending' while unsettled (docs/06 §3)."""
    if any(r == "loss" for r, _ in legs):
        return SlipOutcome("lost", 0.0)  # SportyBet 4.11: any lost selection loses the bet
    if any(r == "pending" for r, _ in legs):
        return SlipOutcome("open", None)
    mult = math.prod(m if m is not None else 1.0 for _, m in legs)
    if all(r == "void" for r, _ in legs):
        return SlipOutcome("void", 1.0)
    if mult <= _TOL:
        return SlipOutcome("lost", 0.0)
    if all(r in ("win", "void") for r, _ in legs):
        return SlipOutcome("won", mult)
    return SlipOutcome("partial", mult)


def reconcile(
    sb: tuple[int, int] | None, db: tuple[int, int] | None
) -> tuple[str, tuple[int, int] | None]:
    """Primary SportyBet vs secondary DB score.

    → ('ok', score) | ('conflict', None) | ('none', None).
    """
    if sb is not None and db is not None:
        return ("ok", sb) if sb == db else ("conflict", None)
    if sb is not None:
        return "ok", sb
    if db is not None:
        return "ok", db
    return "none", None


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


@dataclass
class SettleReport:
    legs_settled: int = 0
    legs_waiting: int = 0
    slips_settled: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    needs_review: list[str] = field(default_factory=list)
    no_result: list[str] = field(default_factory=list)
    unknown_status: list[str] = field(default_factory=list)


class _ResultCache:
    def __init__(self, client: SportyBetClient, tz: ZoneInfo) -> None:
        self.client = client
        self.tz = tz
        self._by_key: dict[tuple[str, date], dict[str, SbResult]] = {}

    def get(self, tournament: str, day: date) -> dict[str, SbResult]:
        key = (tournament, day)
        if key not in self._by_key:
            start, end = day_window_ms(day, self.tz)
            out: dict[str, SbResult] = {}
            page = 1
            while True:
                payload = self.client.t.get_json(
                    RESULTS_PATH,
                    {
                        "pageNum": str(page),
                        "pageSize": str(PAGE_SIZE),
                        "sportId": "sr:sport:1",
                        "startTime": str(start),
                        "endTime": str(end),
                        "tournamentId": tournament,
                    },
                    f"results_{tournament.replace(':', '_')}_{day.isoformat()}_p{page}.json",
                )
                got, total = parse_results(payload)
                out.update(got)
                if page * PAGE_SIZE >= total or not got:
                    break
                page += 1
            self._by_key[key] = out
        return self._by_key[key]


def _sb_result(cache: _ResultCache, league_key: str, match: Match) -> SbResult | None:
    if not match.sportybet_event_id:
        return None
    local_day = match.kickoff_utc.astimezone(cache.tz).date()
    for t in LEAGUES[league_key].sb_tournaments:
        for day in (local_day, local_day + timedelta(days=1)):  # late kickoffs end after midnight
            hit = cache.get(t, day).get(match.sportybet_event_id)
            if hit is not None:
                return hit
    return None


def run_settle(ctx: JobContext) -> str:
    now = utcnow()
    report = SettleReport()
    transport = make_transport(ctx.settings)
    cache = _ResultCache(SportyBetClient(transport), ctx.settings.tz)
    touched_slips: set[int] = set()
    try:
        with session_scope(ctx.db_path) as s:
            rows = s.execute(
                select(SlipLeg, ValueLeg, Match, League)
                .join(ValueLeg, ValueLeg.id == SlipLeg.value_leg_id)
                .join(Match, Match.id == ValueLeg.match_id)
                .join(League, League.id == Match.league_id)
                .where(SlipLeg.result == "pending", Match.kickoff_utc < now - SETTLE_AFTER)
                .order_by(Match.kickoff_utc, SlipLeg.id)
            ).all()
            decided: dict[int, Decision] = {}
            for sl, vl, m, lg in rows:
                if m.id not in decided:
                    decided[m.id] = _decide(cache, lg.key, m, report)
                d = decided[m.id]
                if d.verdict == "wait":
                    report.legs_waiting += 1
                    continue
                score = None if d.verdict == "void" else period_score(d, vl.market)
                if d.verdict == "score" and score is None:
                    report.legs_waiting += 1  # half-time leg, half-time score not known yet
                    report.no_result.append(f"match {m.id}: no half-time score for {vl.market}")
                    continue
                result, mult = settle_leg(vl.market, vl.line, vl.selection, vl.odds, score)
                sl.result, sl.result_multiplier = result, mult
                report.legs_settled += 1
                touched_slips.add(sl.slip_id)
            s.flush()
            for slip_id in sorted(touched_slips):
                slip = s.get_one(Slip, slip_id)
                out = slip_outcome([(leg.result, leg.result_multiplier) for leg in slip.legs])
                if out.status != "open" and slip.status == "open":
                    slip.status, slip.return_multiplier = out.status, out.return_multiplier
                    report.slips_settled[out.status] += 1
    except BlockedError as exc:
        ctx.warn(f"SportyBet blocked the results request ({exc}); nothing more settled this run")
    finally:
        ctx.count("http_requests", getattr(transport, "requests_made", 0))
        transport.close()

    ctx.note("legs_settled", report.legs_settled)
    ctx.note("legs_waiting", report.legs_waiting)
    ctx.note("slips_settled", dict(report.slips_settled))
    if report.needs_review:
        ctx.warn(f"results disagree (needs_review, not settled): {report.needs_review}")
    lines = [
        f"legs settled: {report.legs_settled}   still waiting: {report.legs_waiting}",
        "slips settled: "
        + (", ".join(f"{k} {v}" for k, v in report.slips_settled.items()) or "none"),
    ]
    if report.needs_review:
        lines.append("NEEDS REVIEW (sources disagree): " + "; ".join(report.needs_review))
    if report.no_result:
        lines.append("no result yet: " + "; ".join(report.no_result))
    if report.unknown_status:
        lines.append(
            "unrecognised SportyBet status (left pending): " + "; ".join(report.unknown_status)
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class Decision:
    verdict: str  # score | void | wait
    full_time: tuple[int, int] | None = None
    first_half: tuple[int, int] | None = None  # docs/10 §3; None = unknown


def period_score(d: Decision, market: str) -> tuple[int, int] | None:
    """docs/10 §3: the score a market settles on — full time, first half, or second half
    (full time − first half). None when a half-time score is needed but unknown."""
    _, period = split_period(market)
    if period == "FT":
        return d.full_time
    if d.first_half is None or d.full_time is None:
        return None
    if period == "1H":
        return d.first_half
    return (d.full_time[0] - d.first_half[0], d.full_time[1] - d.first_half[1])


def _decide(cache: _ResultCache, league_key: str, m: Match, report: SettleReport) -> Decision:
    label = f"match {m.id} ({m.sportybet_event_id or 'no SB id'})"
    if m.status == "needs_review":
        report.needs_review.append(label)
        return Decision("wait")
    if m.status in VOID_MATCH_STATUSES:
        return Decision("void")
    try:
        sb = _sb_result(cache, league_key, m)
    except (FetchError, SportyBetPayloadError) as exc:
        report.no_result.append(f"{label}: SportyBet results unavailable ({exc})")
        sb = None
    if sb is not None and not sb.ended:
        report.unknown_status.append(f"{label}: status {sb.status} '{sb.match_status}'")
        return Decision("wait")
    db = (
        (m.home_goals, m.away_goals)
        if m.status == "finished" and m.home_goals is not None and m.away_goals is not None
        else None
    )
    verdict, score = reconcile(sb.score_90 if sb else None, db)
    if verdict == "conflict":
        m.status = "needs_review"
        report.needs_review.append(f"{label}: SportyBet {sb.score_90 if sb else None} vs DB {db}")
        log.warning(
            "results disagree; match marked needs_review", extra={"fields": {"match_id": m.id}}
        )
        return Decision("wait")
    if verdict == "none":
        report.no_result.append(label)
        return Decision("wait")
    assert score is not None
    if db is None:  # store SportyBet's 90-minute score as the match result
        m.home_goals, m.away_goals, m.status = score[0], score[1], "finished"
    # docs/10 §3: first-half score, SportyBet primary / football-data secondary
    db_ht = (
        (m.ht_home_goals, m.ht_away_goals)
        if m.ht_home_goals is not None and m.ht_away_goals is not None
        else None
    )
    ht_verdict, ht = reconcile(sb.score_1h if sb else None, db_ht)
    if ht_verdict == "conflict":
        m.status = "needs_review"
        report.needs_review.append(
            f"{label}: half time SportyBet {sb.score_1h if sb else None} vs DB {db_ht}"
        )
        log.warning(
            "half-time results disagree; match marked needs_review",
            extra={"fields": {"match_id": m.id}},
        )
        return Decision("wait")
    if ht is not None and db_ht is None:
        m.ht_home_goals, m.ht_away_goals = ht
    return Decision("score", score, ht)
