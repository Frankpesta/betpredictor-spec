"""Tuning grid on the tuning seasons + the gate on the holdout (docs/03 §10.3, §10.5). Pure.

Stage 1: (xi, xg_blend_weight) by lowest O/U 2.5 log loss of the raw model probability.
Stage 2: market_shrink_weight by lowest log loss of p_final (O/U 2.5 + half-line AH).
Stage 3: min_edge_leg by highest ROI, only among settings with ≥ min_bets_for_roi bets.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from engine.backtest.walk_forward import (
    MatchMarkets,
    MatchPrice,
    SelectionRecord,
    ValueParams,
    evaluate,
)
from engine.config import GatesCfg
from engine.model.calibration import log_loss


@dataclass(frozen=True)
class Stage1Row:
    xi_per_day: float
    xg_blend_weight: float
    ou_log_loss: float
    n: int


@dataclass(frozen=True)
class Stage2Row:
    market_shrink_weight: float
    log_loss: float
    n: int


@dataclass(frozen=True)
class Stage3Row:
    min_edge_leg: float
    bets: int
    roi: float | None
    profit_units: float
    eligible: bool


@dataclass(frozen=True)
class TuningResult:
    stage1: list[Stage1Row]
    stage2: list[Stage2Row]
    stage3: list[Stage3Row]
    xi_per_day: float
    xg_blend_weight: float
    market_shrink_weight: float
    min_edge_leg: float
    min_edge_fallback: bool  # no setting reached min_bets_for_roi -> kept the configured value


def ou_model_log_loss(
    prices: Sequence[MatchPrice], markets: dict[int, MatchMarkets], w: float
) -> tuple[float, int]:
    rows = [p for p in prices if p.blend_weight == w]
    p_over = np.array([p.p_over for p in rows])
    won = np.array(
        [float(markets[p.match_id].home_goals + markets[p.match_id].away_goals > 2.5) for p in rows]
    )
    return log_loss(p_over, won), len(rows)


def stage1(
    prices_by_xi: dict[float, list[MatchPrice]],
    markets: dict[int, MatchMarkets],
    blend_grid: Sequence[float],
) -> list[Stage1Row]:
    out = []
    for xi, prices in sorted(prices_by_xi.items()):
        for w in blend_grid:
            ll, n = ou_model_log_loss(prices, markets, w)
            out.append(Stage1Row(xi, w, ll, n))
    return out


def canonical_log_loss(records: Sequence[SelectionRecord]) -> tuple[float, int]:
    canon = [r for r in records if r.canonical]
    return (
        log_loss(
            np.array([r.p_final for r in canon]), np.array([r.won for r in canon], dtype=float)
        ),
        len(canon),
    )


def roi_of(records: Sequence[SelectionRecord]) -> tuple[int, float | None, float]:
    bets = [r for r in records if r.qualifies]
    profit = float(sum(r.return_multiplier - 1.0 for r in bets))
    return len(bets), (profit / len(bets) if bets else None), profit


def tune(
    prices_by_xi: dict[float, list[MatchPrice]],
    markets: dict[int, MatchMarkets],
    blend_grid: Sequence[float],
    shrink_grid: Sequence[float],
    edge_grid: Sequence[float],
    base: ValueParams,
    min_bets_for_roi: int,
) -> TuningResult:
    s1 = stage1(prices_by_xi, markets, blend_grid)
    best1 = min(s1, key=lambda r: (r.ou_log_loss, r.xi_per_day, r.xg_blend_weight))
    prices = prices_by_xi[best1.xi_per_day]

    s2 = []
    for s in shrink_grid:
        recs = evaluate(prices, markets, best1.xg_blend_weight, replace(base, shrink_weight=s))
        ll, n = canonical_log_loss(recs)
        s2.append(Stage2Row(s, ll, n))
    best2 = min(s2, key=lambda r: (r.log_loss, r.market_shrink_weight))

    s3 = []
    for e in edge_grid:
        recs = evaluate(
            prices,
            markets,
            best1.xg_blend_weight,
            replace(base, shrink_weight=best2.market_shrink_weight, min_edge=e),
        )
        bets, roi, profit = roi_of(recs)
        s3.append(Stage3Row(e, bets, roi, profit, bets >= min_bets_for_roi))
    eligible = [r for r in s3 if r.eligible and r.roi is not None]
    if eligible:
        best_edge = max(eligible, key=lambda r: (r.roi or 0.0, -r.min_edge_leg)).min_edge_leg
        fallback = False
    else:
        best_edge, fallback = base.min_edge, True

    return TuningResult(
        stage1=s1,
        stage2=s2,
        stage3=s3,
        xi_per_day=best1.xi_per_day,
        xg_blend_weight=best1.xg_blend_weight,
        market_shrink_weight=best2.market_shrink_weight,
        min_edge_leg=best_edge,
        min_edge_fallback=fallback,
    )


@dataclass(frozen=True)
class GateResult:
    passed: bool
    ece: float | None
    bets: int
    roi: float | None
    mean_clv: float | None
    failures: list[str]


def gate(holdout_overall: dict[str, Any], gates: GatesCfg) -> GateResult:
    """docs/03 §10.5 on the holdout, overall (both markets pooled)."""
    ece = holdout_overall.get("ece")
    bets = int(holdout_overall.get("bets", 0))
    roi = holdout_overall.get("roi")
    failures = []
    if ece is None or ece > gates.max_calibration_ece:
        failures.append(f"ECE {ece} > {gates.max_calibration_ece}")
    if bets < gates.min_bets_for_roi:
        failures.append(f"bets {bets} < {gates.min_bets_for_roi}")
    if roi is None or roi <= gates.min_roi:
        failures.append(f"ROI {roi} <= {gates.min_roi}")
    if gates.require_beats_market_logloss:
        ll, base = holdout_overall.get("log_loss"), holdout_overall.get("baseline_log_loss_open")
        if ll is None or base is None or ll >= base:
            failures.append(f"log loss {ll} does not beat market {base}")
    return GateResult(not failures, ece, bets, roi, holdout_overall.get("mean_clv"), failures)
