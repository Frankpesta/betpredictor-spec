"""docs/05 §2.3 value tests (exact cases) + sanity/qualifying rules."""

from __future__ import annotations

import pytest

from engine.value.edge import devig_pair, edge, half_line_expected_multiplier, overround, shrink
from engine.value.sanity import SanityInput, qualifies, sanity_reasons


@pytest.mark.parametrize(
    ("oa", "ob", "pa", "pb"), [(1.90, 1.90, 0.5, 0.5), (1.50, 2.60, 0.6341, 0.3659)]
)
def test_devig(oa: float, ob: float, pa: float, pb: float) -> None:
    a, b = devig_pair(oa, ob)
    assert a == pytest.approx(pa, abs=1e-4) and b == pytest.approx(pb, abs=1e-4)
    assert a + b == pytest.approx(1.0, abs=1e-12)


def test_shrink() -> None:
    assert shrink(0.70, 0.60, 0.7) == pytest.approx(0.67, abs=1e-12)


def test_edge() -> None:
    em = half_line_expected_multiplier(0.67, 1.60)
    assert em == pytest.approx(1.072, abs=1e-12)
    assert edge(em) == pytest.approx(0.072, abs=1e-12)


def _inp(**kw: object) -> SanityInput:
    base: dict[str, object] = {
        "line": 2.5,
        "p_model": 0.60,
        "p_market_devig": 0.60,
        "overround": 1.05,
        "low_confidence": False,
        "minutes_to_kickoff": 120.0,
        "stale_prediction": False,
    }
    base.update(kw)
    return SanityInput(**base)  # type: ignore[arg-type]


def test_gap_flag_threshold() -> None:
    assert "model_market_gap" in sanity_reasons(_inp(p_model=0.751), 0.15)
    assert "model_market_gap" not in sanity_reasons(_inp(p_model=0.749), 0.15)
    # docs/05 §2.3 phrasing: gaps of 0.151 vs 0.149
    assert "model_market_gap" in sanity_reasons(
        _inp(p_model=0.20 + 0.151, p_market_devig=0.20), 0.15
    )
    assert sanity_reasons(_inp(p_model=0.20 + 0.149, p_market_devig=0.20), 0.15) == []


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"line": 2.25}, "non_half_line_v1"),
        ({"line": -1.0}, "non_half_line_v1"),
        ({"p_market_devig": None}, "missing_pair"),
        ({"low_confidence": True}, "low_confidence_team"),
        ({"minutes_to_kickoff": 14.9}, "too_close_to_kickoff"),
        ({"minutes_to_kickoff": -5.0}, "too_close_to_kickoff"),
        ({"stale_prediction": True}, "stale_prediction"),
        ({"overround": 0.99}, "bad_overround"),
        ({"overround": 1.151}, "bad_overround"),
    ],
)
def test_each_sanity_reason(kw: dict[str, object], reason: str) -> None:
    assert reason in sanity_reasons(_inp(**kw), 0.15)


def test_backtest_inputs_skip_live_only_checks() -> None:
    assert sanity_reasons(_inp(minutes_to_kickoff=None), 0.15) == []


def test_overround() -> None:
    assert overround(1.90, 1.90) == pytest.approx(2 / 1.9)


def test_qualifies() -> None:
    assert qualifies([], 0.03, 1.8, 0.03, 1.2, 2.6)
    assert not qualifies([], 0.0299, 1.8, 0.03, 1.2, 2.6)
    assert not qualifies(["model_market_gap"], 0.2, 1.8, 0.03, 1.2, 2.6)
    assert not qualifies([], 0.1, 2.61, 0.03, 1.2, 2.6)
    assert not qualifies([], 0.1, 1.19, 0.03, 1.2, 2.6)
    assert qualifies([], 0.1, 2.6, 0.03, 1.2, 2.6)
