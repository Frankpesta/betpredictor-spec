"""docs/05 §8-9 — "likeliest" and "data_rule" selection: qualification, slip builders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from engine.config import get_settings
from engine.slips.builder import best_daily_2odds, mega_acca, mid_acca
from engine.slips.constraints import Leg
from engine.value.selection import (
    TeamData,
    data_reason,
    favourite_side,
    p_scores,
    qualifies_data_rule,
    qualifies_likeliest,
    team_data,
)

NOW = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
SLIPS = get_settings().slips
DR = get_settings().value.data_rule


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


# ---- docs/05 §9: "data_rule" ----
STRONG = TeamData(0.93, 1, 10)  # scores 93%, blanked 1/10
WEAK = TeamData(0.55, 5, 10)  # scores 55%, blanked 5/10
MID = TeamData(0.80, 3, 10)  # neither strong nor weak, not an Over side (3 blanks)
OVERISH = TeamData(0.75, 2, 10)  # both-score side, not strong
FEW_GAMES = TeamData(0.95, 0, 3)  # strong numbers, too few games


def test_p_scores_and_team_data() -> None:
    mat = np.array([[0.10, 0.05], [0.25, 0.60]])  # rows = home goals
    p_home, p_away = p_scores(mat)
    assert p_home == pytest.approx(0.85)  # 1 - P(home 0) = 1 - (0.10 + 0.05)
    assert p_away == pytest.approx(0.65)  # 1 - P(away 0) = 1 - (0.10 + 0.25)
    assert team_data(0.7, [0, 2, 0, 1]) == TeamData(0.7, 2, 4)
    assert team_data(0.7, []) == TeamData(0.7, 0, 0)


@pytest.mark.parametrize(
    ("market", "line", "selection", "home", "away", "starts"),
    [
        ("AH", -0.5, "home", STRONG, MID, "to win: home"),  # home gives goals, strong attack
        ("AH", -1.5, "home", MID, MID, None),  # giving goals without a strong attack
        ("AH", -1.5, "home", STRONG, MID, "to win by 2+: home"),
        ("AH", 2.5, "away", MID, STRONG, "to win by 3+: away"),
        ("AH", 0.5, "away", MID, STRONG, "to win: away"),  # home +0.5 = away -0.5
        ("AH", -0.5, "home", FEW_GAMES, MID, None),  # too few games for "strong"
        ("AH", 0.5, "home", MID, WEAK, "vs weak attack: away"),  # home takes goals vs weak
        ("AH", -0.5, "away", WEAK, MID, "vs weak attack: home"),  # away +0.5 vs weak home
        ("AH", 0.5, "home", MID, MID, None),  # head start without a weak opponent
        ("OU", 2.5, "under", MID, WEAK, "weak attack: away"),
        ("OU", 2.5, "under", WEAK, WEAK, "weak attack: home"),  # lists both
        ("OU", 2.5, "under", STRONG, MID, None),
        ("OU", 1.5, "over", OVERISH, STRONG, "both score"),
        ("OU", 1.5, "over", OVERISH, MID, None),  # 3 blanks: not reliable
        ("OU", 1.5, "over", STRONG, WEAK, None),
    ],
)
def test_data_reason(
    market: str, line: float, selection: str, home: TeamData, away: TeamData, starts: str | None
) -> None:
    why = data_reason(market, line, selection, home, away, DR)
    if starts is None:
        assert why is None
    else:
        assert why is not None and why.startswith(starts)


def test_data_reason_shows_the_numbers() -> None:
    why = data_reason("OU", 2.5, "under", WEAK, WEAK, DR)
    assert why == "weak attack: home scores 55%, blanked 5/10; away scores 55%, blanked 5/10"


@pytest.mark.parametrize(
    ("reasons", "odds", "p_final", "expected"),
    [
        ((), 1.40, 0.70, True),
        ((), 1.02, 0.95, False),  # below the odds floor: the 1.02 "certainties"
        ((), DR.min_odds, 0.70, True),  # floor is inclusive
        ((), 2.10, 0.45, False),  # below min_leg_probability
        (("model_market_gap",), 1.40, 0.70, False),  # sanity flags still exclude
    ],
)
def test_qualifies_data_rule(
    reasons: tuple[str, ...], odds: float, p_final: float, expected: bool
) -> None:
    why = qualifies_data_rule(reasons, "OU", 2.5, "under", odds, p_final, MID, WEAK, DR)
    assert (why is not None) is expected


def test_builders_data_rule_use_qualifies_without_edge_floor() -> None:
    legs = [leg(i, 1.30, 0.75 - i / 100) for i in range(1, 11)]  # EM < 1 for all
    legs.append(leg(20, 1.40, 0.90, qualifies=False))  # no data reason: never used
    chosen = mid_acca(legs, NOW, SLIPS.mid_acca, strategy="data_rule")
    assert chosen is not None
    assert [lg.match_id for lg in chosen.legs] == list(range(1, SLIPS.mid_acca.max_legs + 1))
    window = (NOW, NOW + timedelta(days=2))
    v = get_settings().value
    mega = mega_acca(legs, window, SLIPS.mega_acca, v.min_odds, v.max_odds, strategy="data_rule")
    assert mega is not None and 20 not in {lg.match_id for lg in mega.legs}
