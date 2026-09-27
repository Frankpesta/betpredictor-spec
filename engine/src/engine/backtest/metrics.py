"""Backtest metrics (docs/03 §10.4). Pure: SelectionRecords in, JSON-able dicts out.

Probability metrics (log loss, Brier, ECE) use one row per priced half-line market
(the `canonical` side: over / home) — the other side is its complement.
Betting metrics use every qualifying selection, flat 1-unit stakes, in kickoff order.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from engine.backtest.walk_forward import SelectionRecord
from engine.config import Daily2OddsCfg
from engine.model.calibration import brier, ece, log_loss, reliability_table
from engine.slips.builder import best_daily_2odds
from engine.slips.constraints import Leg


def _prob_metrics(canon: Sequence[SelectionRecord]) -> dict[str, Any]:
    if not canon:
        return {"n_priced": 0}
    p = np.array([r.p_final for r in canon])
    pm = np.array([r.p_model for r in canon])
    o = np.array([r.won for r in canon], dtype=float)
    dev = np.array([r.p_market_devig for r in canon])
    out: dict[str, Any] = {
        "n_priced": len(canon),
        "log_loss": log_loss(p, o),
        "log_loss_model_raw": log_loss(pm, o),
        "brier": brier(p, o),
        "ece": ece(p, o),
        "reliability": [asdict(b) for b in reliability_table(p, o)],
        "baseline_log_loss_open": log_loss(dev, o),
    }
    closed = [r for r in canon if r.p_close_devig is not None]
    if closed:
        oc = np.array([r.won for r in closed], dtype=float)
        out["closing_subset_n"] = len(closed)
        out["baseline_log_loss_close"] = log_loss(
            np.array([r.p_close_devig for r in closed], dtype=float), oc
        )
        out["log_loss_on_closing_subset"] = log_loss(np.array([r.p_final for r in closed]), oc)
        by_src: dict[str, int] = defaultdict(int)
        for r in closed:
            by_src[r.closing_source or "?"] += 1
        out["closing_subset_by_source"] = dict(by_src)
    return out


def _bet_metrics(bets: Sequence[SelectionRecord], primary_closing: str) -> dict[str, Any]:
    bets = sorted(bets, key=lambda r: (r.kickoff, r.match_id, r.market, r.selection))
    n = len(bets)
    if n == 0:
        return {"bets": 0}
    profits = np.array([r.return_multiplier - 1.0 for r in bets])
    cum = np.cumsum(profits)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    streak = longest = 0
    for r in bets:
        streak = streak + 1 if r.return_multiplier < 1.0 else 0
        longest = max(longest, streak)
    out: dict[str, Any] = {
        "bets": n,
        "hit_rate": float(np.mean([r.won for r in bets])),
        "profit_units": float(profits.sum()),
        "roi": float(profits.sum() / n),
        "max_drawdown_units": float(np.max(peak - cum)),
        "longest_losing_streak": longest,
        "mean_odds": float(np.mean([r.odds for r in bets])),
        "mean_edge_claimed": float(np.mean([r.edge for r in bets])),
    }
    with_close = [r for r in bets if r.closing_odds is not None]
    if with_close:
        clv = [r.odds / r.closing_odds - 1.0 for r in with_close if r.closing_odds]
        out["mean_clv"] = float(np.mean(clv))
        out["clv_n"] = len(clv)
        primary = [
            r.odds / r.closing_odds - 1.0
            for r in with_close
            if r.closing_odds and r.closing_source == primary_closing
        ]
        out[f"mean_clv_{primary_closing}_only"] = float(np.mean(primary)) if primary else None
        out[f"clv_n_{primary_closing}_only"] = len(primary)
        src: dict[str, int] = defaultdict(int)
        for r in with_close:
            src[r.closing_source or "?"] += 1
        out["clv_n_by_source"] = dict(src)
    out["clv_missing"] = n - len(with_close)
    return out


def scope_metrics(recs: Sequence[SelectionRecord], primary_closing: str) -> dict[str, Any]:
    return {
        **_prob_metrics([r for r in recs if r.canonical]),
        **_bet_metrics([r for r in recs if r.qualifies], primary_closing),
    }


def all_metrics(recs: Sequence[SelectionRecord], primary_closing: str) -> dict[str, Any]:
    """{scope: {market: metrics}} for scope in overall + each league, market in ALL/OU/AH."""
    scopes: dict[str, list[SelectionRecord]] = {"overall": list(recs)}
    for r in recs:
        scopes.setdefault(r.league, []).append(r)
    out: dict[str, Any] = {}
    for scope, rs in scopes.items():
        out[scope] = {
            "ALL": scope_metrics(rs, primary_closing),
            "OU": scope_metrics([r for r in rs if r.market == "OU"], primary_closing),
            "AH": scope_metrics([r for r in rs if r.market == "AH"], primary_closing),
        }
    return out


def _leg(r: SelectionRecord) -> Leg:
    return Leg(
        match_id=r.match_id,
        market=r.market,
        line=r.line,
        selection=r.selection,
        odds=r.odds,
        p_final=r.p_final,
        expected_multiplier=r.expected_multiplier,
        kickoff_utc=datetime.fromtimestamp(r.kickoff, UTC),
        qualifies=r.qualifies,
    )


@dataclass(frozen=True)
class SlipOutcome:
    day: str
    legs: int
    total_odds: float
    p_all_win: float
    won: bool

    @property
    def return_multiplier(self) -> float:
        return self.total_odds if self.won else 0.0


def simulate_daily_slips(
    recs: Sequence[SelectionRecord], cfg: Daily2OddsCfg, tz: ZoneInfo
) -> dict[str, Any]:
    """docs/03 §10.4: one daily-2-odds slip per local date from that day's qualifying legs."""
    by_day: dict[str, list[Leg]] = defaultdict(list)
    for r in recs:
        if r.qualifies:
            day = datetime.fromtimestamp(r.kickoff, UTC).astimezone(tz).date().isoformat()
            by_day[day].append(_leg(r))
    won_lookup = {(r.match_id, r.market, r.line, r.selection): r.won for r in recs}
    slips: list[SlipOutcome] = []
    for day in sorted(by_day):
        chosen = best_daily_2odds(by_day[day], cfg)
        if chosen is None:
            continue
        all_won = all(
            won_lookup[(lg.match_id, lg.market, lg.line, lg.selection)] for lg in chosen.legs
        )
        slips.append(
            SlipOutcome(
                day=day,
                legs=len(chosen.legs),
                total_odds=chosen.totals.total_odds,
                p_all_win=chosen.totals.p_all_win,
                won=all_won,
            )
        )
    n = len(slips)
    profit = sum(sl.return_multiplier - 1.0 for sl in slips)
    return {
        "days_with_qualifying_legs": len(by_day),
        "slips": n,
        "hit_rate": (sum(sl.won for sl in slips) / n) if n else None,
        "mean_p_all_win": (sum(sl.p_all_win for sl in slips) / n) if n else None,
        "profit_units": profit,
        "roi": profit / n if n else None,
    }


def ou_calibration_without_odds(
    p_over: Sequence[float], went_over: Sequence[int], base_rate: float
) -> dict[str, Any]:
    """INTL (docs/09 §4): no odds exist, so only probability quality is measurable.

    Baseline = a constant forecast of the over-2.5 frequency before the test period.
    """
    p = np.array(p_over, dtype=float)
    o = np.array(went_over, dtype=float)
    if p.size == 0:
        return {"n_priced": 0}
    return {
        "n_priced": int(p.size),
        "log_loss": log_loss(p, o),
        "brier": brier(p, o),
        "ece": ece(p, o),
        "reliability": [asdict(b) for b in reliability_table(p, o)],
        "baseline_base_rate": base_rate,
        "baseline_log_loss_base_rate": log_loss(np.full(p.size, base_rate), o),
        "observed_over_rate": float(o.mean()),
        "roi": None,
        "clv": None,
        "gate": "not computable (no historical odds)",
    }
