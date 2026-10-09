"""Sanity checks on value legs (docs/05 §2.1) and the qualifying rule (§2.2).

Pure: callers pass in the facts; checks return reason codes. A leg with any
reason is `flagged` (stored, never deleted, never used in a slip).
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.model.markets import classify_line

# docs/05 §2.1: acceptable two-way overround range, and minimum lead time.
OVERROUND_MIN = 1.00
OVERROUND_MAX = 1.15
MIN_MINUTES_TO_KICKOFF = 15

REASON_NON_HALF_LINE = "non_half_line_v1"
REASON_GAP = "model_market_gap"
REASON_LOW_CONFIDENCE = "low_confidence_team"
REASON_TOO_CLOSE = "too_close_to_kickoff"
REASON_STALE = "stale_prediction"
REASON_OVERROUND = "bad_overround"
REASON_MISSING_PAIR = "missing_pair"


@dataclass(frozen=True)
class SanityInput:
    line: float
    p_model: float
    p_market_devig: float | None  # None when the opposite selection is missing
    overround: float | None
    low_confidence: bool
    minutes_to_kickoff: float | None  # None in the backtest (not applicable)
    stale_prediction: bool
    lineless: bool = False  # 1X2 / DC / BTTS (docs/05 §10): no line to check


def sanity_reasons(x: SanityInput, max_model_market_gap: float) -> list[str]:
    """All failed checks, in the order of docs/05 §2 (line filter first)."""
    reasons: list[str] = []
    if not x.lineless and classify_line(x.line) != "half":
        reasons.append(REASON_NON_HALF_LINE)
    if x.p_market_devig is None:
        reasons.append(REASON_MISSING_PAIR)
    elif abs(x.p_model - x.p_market_devig) > max_model_market_gap:
        reasons.append(REASON_GAP)
    if x.low_confidence:
        reasons.append(REASON_LOW_CONFIDENCE)
    if x.minutes_to_kickoff is not None and x.minutes_to_kickoff < MIN_MINUTES_TO_KICKOFF:
        reasons.append(REASON_TOO_CLOSE)
    if x.stale_prediction:
        reasons.append(REASON_STALE)
    if x.overround is not None and not (OVERROUND_MIN <= x.overround <= OVERROUND_MAX):
        reasons.append(REASON_OVERROUND)
    return reasons


def qualifies(
    reasons: list[str], edge: float, odds: float, min_edge: float, min_odds: float, max_odds: float
) -> bool:
    """docs/05 §2.2: sanity ok, edge ≥ min_edge_leg, min_odds ≤ odds ≤ max_odds."""
    return not reasons and edge >= min_edge and min_odds <= odds <= max_odds
