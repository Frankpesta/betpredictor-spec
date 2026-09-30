"""docs/05 §8 — "likeliest" selection: favourite side, qualification, slip builders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.config import get_settings
from engine.slips.builder import best_daily_2odds, mega_acca, mid_acca
from engine.slips.constraints import Leg
from engine.value.selection import favourite_side, qualifies_likeliest

NOW = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
SLIPS = get_settings().slips


@pytest.mark.parametrize(
    ("ah_home_p", "expected"),
    [
        # both ±0.5: P(home win)=0.60 vs P(away win)=1-0.85=0.15
        ({-0.5: 0.60, 0.5: 0.85}, "home"),
        # P(home win)=0.20 vs P(away win)=1-0.45=0.55
        ({-0.5: 0.20, 0.5: 0.45}, "away"),
        # exact tie
        ({-0.5: 0.30, 0.5: 0.70}, None),
        # big home favourite, no ±0.5 lines: fair line -2.5 (p 0.52) -> home
        ({-1.5: 0.70, -2.5: 0.52, -3.5: 0.33}, "home"),
        # away favourite, only positive home lines: fair line +1.5 (p 0.49) -> away
        ({1.5: 0.49, 2.5: 0.70}, "away"),
        # whole/quarter lines are ignored; nothing left -> None
        ({0.0: 0.6, -1.0: 0.4}, None),
        ({}, None),
    ],
)
def test_favourite_side(ah_home_p: dict[float, float], expected: str | None) -> None:
    assert favourite_side(ah_home_p) == expected


@pytest.mark.parametrize(
    ("reasons", "market", "selection", "favourite", "expected"),
    [
        ((), "OU", "over", None, True),  # goals legs never need a favourite
        ((), "OU", "under", "home", True),
        ((), "AH", "home", "home", True),  # favourite's side, any line
        ((), "AH", "away", "home", False),  # weaker team's head start: not taken
        ((), "AH", "away", "away", True),
        ((), "AH", "home", None, False),  # favourite unknown -> no AH legs
        (("model_market_gap",), "OU", "over", None, False),  # sanity flags still exclude
        (("non_half_line_v1",), "AH", "home", "home", False),
    ],
)
def test_qualifies_likeliest(
    reasons: tuple[str, ...], market: str, selection: str, favourite: str | None, expected: bool
) -> None:
    assert qualifies_likeliest(reasons, market, selection, favourite) is expected  # type: ignore[arg-type]


def leg(match_id: int, odds: float, p: float, *, qualifies: bool = True, hours: float = 6) -> Leg:
    return Leg(
        match_id=match_id,
        market="OU",
        line=1.5,
        selection="over",
        odds=odds,
        p_final=p,
        expected_multiplier=p * odds,
        kickoff_utc=NOW + timedelta(hours=hours),
        qualifies=qualifies,
        sanity_ok=True,
    )


def test_daily_likeliest_ignores_slip_edge_and_skips_unreachable_short_prices() -> None:
    # negative-edge legs (EM < 1) — "value" builds nothing, "likeliest" does
    legs = [leg(1, 1.40, 0.70), leg(2, 1.42, 0.69), leg(3, 1.35, 0.72)]
    legs += [leg(10 + i, 1.03, 0.95) for i in range(70)]  # would fill the 60-leg cap
    assert best_daily_2odds(legs, SLIPS.daily_2odds, strategy="value") is None
    chosen = best_daily_2odds(legs, SLIPS.daily_2odds, strategy="likeliest")
    assert chosen is not None
    assert {lg.match_id for lg in chosen.legs} == {1, 3}  # highest p_all_win in range
    cfg = SLIPS.daily_2odds
    assert cfg.target_odds_min <= chosen.totals.total_odds <= cfg.target_odds_max


def test_mid_likeliest_takes_the_likeliest_legs_without_edge_floor() -> None:
    legs = [leg(i, 1.10, 0.88 - i / 100) for i in range(1, 11)]  # EM < 1 for all
    legs.append(leg(20, 1.08, 0.99, qualifies=False))  # never used
    assert mid_acca(legs, NOW, SLIPS.mid_acca, strategy="value") is None
    chosen = mid_acca(legs, NOW, SLIPS.mid_acca, strategy="likeliest")
    assert chosen is not None
    assert [lg.match_id for lg in chosen.legs] == list(range(1, SLIPS.mid_acca.max_legs + 1))


def test_mega_likeliest_ignores_odds_bounds_and_edge() -> None:
    window = (NOW, NOW + timedelta(days=2))
    legs = [leg(1, 1.02, 0.97), leg(2, 1.05, 0.93), leg(3, 1.30, 0.50)]
    v = get_settings().value
    value_slip = mega_acca(legs, window, SLIPS.mega_acca, v.min_odds, v.max_odds, strategy="value")
    assert value_slip is None  # 1.02 / 1.05 are below min_odds
    chosen = mega_acca(legs, window, SLIPS.mega_acca, v.min_odds, v.max_odds, strategy="likeliest")
    assert chosen is not None
    assert [lg.match_id for lg in chosen.legs] == [1, 2]  # leg 3 below min_leg_probability
