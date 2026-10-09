"""docs/05 §10 — team goals, BTTS, 1X2, double chance: settlement, pricing, devig, data rule."""

from __future__ import annotations

import numpy as np
import pytest

from engine.config import get_settings
from engine.model.markets import Market, Selection, is_binary, price_selection, settle
from engine.model.score_matrix import score_matrix
from engine.value.edge import devig_group, evaluate_group, group_overround
from engine.value.selection import TeamData, data_reason, qualifies_likeliest

DR = get_settings().value.data_rule


@pytest.mark.parametrize(
    ("market", "selection", "line", "hg", "ag", "expected"),
    [
        ("1X2", "home", 0.0, 2, 1, "win"),
        ("1X2", "home", 0.0, 1, 1, "loss"),
        ("1X2", "draw", 0.0, 0, 0, "win"),
        ("1X2", "away", 0.0, 0, 3, "win"),
        ("DC", "home_draw", 0.0, 1, 1, "win"),
        ("DC", "home_draw", 0.0, 0, 1, "loss"),
        ("DC", "home_away", 0.0, 2, 2, "loss"),
        ("DC", "home_away", 0.0, 0, 1, "win"),
        ("DC", "draw_away", 0.0, 3, 0, "loss"),
        ("BTTS", "yes", 0.0, 1, 1, "win"),
        ("BTTS", "yes", 0.0, 3, 0, "loss"),
        ("BTTS", "no", 0.0, 0, 0, "win"),
        ("BTTS", "no", 0.0, 2, 1, "loss"),
        ("OU_HOME", "over", 1.5, 2, 0, "win"),
        ("OU_HOME", "over", 1.5, 1, 5, "loss"),  # away goals never count
        ("OU_HOME", "under", 0.5, 0, 4, "win"),
        ("OU_AWAY", "over", 0.5, 0, 1, "win"),
        ("OU_AWAY", "under", 1.5, 6, 2, "loss"),
        ("OU_AWAY", "over", 1.0, 3, 1, "push"),  # whole line: same machinery as OU
    ],
)
def test_settle_new_markets(
    market: Market, selection: Selection, line: float, hg: int, ag: int, expected: str
) -> None:
    assert settle(market, selection, line, hg, ag) == expected


def test_settle_rejects_wrong_selection() -> None:
    with pytest.raises(ValueError, match="invalid market/selection"):
        settle("BTTS", "over", 0.0, 1, 1)
    with pytest.raises(ValueError, match="invalid market/selection"):
        settle("DC", "home", 0.0, 1, 1)


def test_pricing_sums_and_identities() -> None:
    mat = score_matrix(1.6, 1.1, -0.05, 25)
    x12 = [price_selection(mat, "1X2", s, 0.0).p_win for s in ("home", "draw", "away")]
    assert sum(x12) == pytest.approx(1.0)
    dc = [price_selection(mat, "DC", s, 0.0).p_win for s in ("home_draw", "home_away", "draw_away")]
    assert sum(dc) == pytest.approx(2.0)
    assert dc[0] == pytest.approx(x12[0] + x12[1])
    # 1X2 home == AH home -0.5; double chance home/draw == AH home +0.5
    assert x12[0] == pytest.approx(price_selection(mat, "AH", "home", -0.5).p_win)
    assert dc[0] == pytest.approx(price_selection(mat, "AH", "home", 0.5).p_win)
    yes = price_selection(mat, "BTTS", "yes", 0.0).p_win
    # P(both score) = 1 − P(home 0) − P(away 0) + P(0-0)
    assert yes == pytest.approx(1 - mat[0, :].sum() - mat[:, 0].sum() + mat[0, 0])
    home_scores = price_selection(mat, "OU_HOME", "over", 0.5).p_win
    assert home_scores == pytest.approx(1 - mat[0, :].sum())
    away_u15 = price_selection(mat, "OU_AWAY", "under", 1.5).p_win
    assert away_u15 == pytest.approx(mat[:, 0].sum() + mat[:, 1].sum())
    probs = price_selection(mat, "BTTS", "no", 0.0)
    assert probs.p_push == probs.p_half_win == probs.p_half_loss == 0.0


@pytest.mark.parametrize(
    ("market", "line", "expected"),
    [
        ("1X2", 0.0, True),
        ("DC", 0.0, True),
        ("BTTS", 0.0, True),
        ("OU_HOME", 1.5, True),
        ("OU_AWAY", 1.0, False),
        ("AH", -0.25, False),
    ],
)
def test_is_binary(market: str, line: float, expected: bool) -> None:
    assert is_binary(market, line) is expected


def test_devig_group_three_way_and_double_chance() -> None:
    p = devig_group((2.0, 4.0, 4.0))  # q = .5 .25 .25 -> sum 1: no margin
    assert p == pytest.approx((0.5, 0.25, 0.25))
    p = devig_group((1.44, 5.02, 7.81))  # Arsenal v Leeds 2026-10-08
    assert sum(p) == pytest.approx(1.0)
    dc = devig_group((1.11, 1.19, 2.70), total=2.0)
    assert sum(dc) == pytest.approx(2.0)
    assert group_overround((1.11, 1.19, 2.70), total=2.0) == pytest.approx(
        (1 / 1.11 + 1 / 1.19 + 1 / 2.70) / 2
    )
    with pytest.raises(ValueError):
        devig_group((1.0, 3.0, 3.0))


def test_evaluate_group_lineless_is_binary_and_not_flagged_for_line() -> None:
    mat = score_matrix(1.6, 1.1, -0.05, 25)
    sels = ("home", "draw", "away")
    legs = evaluate_group(
        "1X2",
        0.0,
        sels,
        tuple(price_selection(mat, "1X2", s, 0.0) for s in sels),
        (1.80, 3.80, 4.60),
        low_confidence=False,
        minutes_to_kickoff=600,
        stale_prediction=False,
        shrink_weight=0.3,
        min_edge=0.03,
        min_odds=1.2,
        max_odds=2.6,
        max_model_market_gap=0.5,
    )
    assert [lv.selection for lv in legs] == list(sels)
    assert all("non_half_line_v1" not in lv.reasons for lv in legs)
    for lv in legs:
        assert lv.expected_multiplier == pytest.approx(lv.p_final * lv.odds)
    assert sum(lv.p_market_devig for lv in legs) == pytest.approx(1.0)


STRONG = TeamData(0.93, 1, 10)
WEAK = TeamData(0.55, 5, 10)
MID = TeamData(0.80, 3, 10)
BOTH = TeamData(0.75, 2, 10)


@pytest.mark.parametrize(
    ("market", "line", "selection", "home", "away", "starts"),
    [
        ("1X2", 0.0, "home", STRONG, MID, "to win: home"),
        ("1X2", 0.0, "away", MID, MID, None),
        ("1X2", 0.0, "draw", STRONG, STRONG, None),  # never a data reason for the draw
        ("DC", 0.0, "home_draw", MID, WEAK, "vs weak attack: away"),
        ("DC", 0.0, "draw_away", WEAK, MID, "vs weak attack: home"),
        ("DC", 0.0, "home_away", WEAK, WEAK, None),
        ("BTTS", 0.0, "yes", BOTH, STRONG, "both score"),
        ("BTTS", 0.0, "yes", BOTH, MID, None),
        ("BTTS", 0.0, "no", MID, WEAK, "weak attack: away"),
        ("OU_HOME", 0.5, "over", STRONG, WEAK, "to score: home"),
        ("OU_HOME", 1.5, "over", STRONG, WEAK, "to score 2+: home"),
        ("OU_HOME", 1.5, "over", MID, WEAK, None),
        ("OU_AWAY", 0.5, "under", STRONG, WEAK, "weak attack: away"),
        ("OU_AWAY", 1.5, "under", WEAK, MID, None),  # the home team being weak is irrelevant
    ],
)
def test_data_reason_new_markets(
    market: str, line: float, selection: str, home: TeamData, away: TeamData, starts: str | None
) -> None:
    why = data_reason(market, line, selection, home, away, DR)
    if starts is None:
        assert why is None
    else:
        assert why is not None and why.startswith(starts)


def test_likeliest_never_takes_new_markets() -> None:
    assert qualifies_likeliest((), "BTTS", "yes", None) is False
    assert qualifies_likeliest((), "OU", "over", None) is True


def test_mat_orientation_guard() -> None:
    mat = np.zeros((3, 3))
    mat[2, 0] = 1.0  # home 2 - away 0
    assert price_selection(mat, "OU_HOME", "over", 1.5).p_win == 1.0
    assert price_selection(mat, "OU_AWAY", "under", 0.5).p_win == 1.0
    assert price_selection(mat, "BTTS", "no", 0.0).p_win == 1.0
