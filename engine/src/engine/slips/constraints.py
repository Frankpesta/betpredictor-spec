"""Shared slip constraints and tie-break ordering (docs/05 §3). Pure."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

# docs/05 §3: legs kicking off sooner than this after slip generation are excluded.
MIN_LEAD = timedelta(minutes=15)
# docs/04 §3.6: max selections per SportyBet betslip — 50, confirmed by the user and
# `maxSelection` in the site config (docs/discovered/sportybet/2026-09-27/endpoints.md).
SPORTYBET_MAX_SELECTIONS = 50


@dataclass(frozen=True)
class Leg:
    match_id: int
    market: str
    line: float
    selection: str
    odds: float
    p_final: float
    expected_multiplier: float
    kickoff_utc: datetime
    # docs/05 §2.2 under "value" (sanity ok + edge >= min_edge_leg + odds bounds);
    # docs/05 §8 under "likeliest" (sanity ok + AH only on the favourite's side)
    qualifies: bool
    sanity_ok: bool = True  # docs/05 §2.1 only (the mega acca uses its own edge floor)

    @property
    def edge(self) -> float:
        return self.expected_multiplier - 1.0


def tie_break_key(leg: Leg) -> tuple[float, float, datetime, int, str, float, str]:
    """Higher p_final, higher edge, earlier kickoff, lower match_id, market, line, selection."""
    return (
        -leg.p_final,
        -leg.edge,
        leg.kickoff_utc,
        leg.match_id,
        leg.market,
        leg.line,
        leg.selection,
    )


def sort_legs(legs: Sequence[Leg]) -> list[Leg]:
    return sorted(legs, key=tie_break_key)


def distinct_matches(legs: Sequence[Leg]) -> bool:
    return len({leg.match_id for leg in legs}) == len(legs)


@dataclass(frozen=True)
class SlipTotals:
    total_odds: float
    p_all_win: float
    expected_multiplier: float

    @property
    def edge(self) -> float:
        return self.expected_multiplier - 1.0


def slip_totals(legs: Sequence[Leg]) -> SlipTotals:
    """Products over independent legs (docs/05 §3)."""
    return SlipTotals(
        total_odds=math.prod(leg.odds for leg in legs),
        p_all_win=math.prod(leg.p_final for leg in legs),
        expected_multiplier=math.prod(leg.expected_multiplier for leg in legs),
    )


def in_window(leg: Leg, start: datetime, end: datetime) -> bool:
    return start <= leg.kickoff_utc <= end
