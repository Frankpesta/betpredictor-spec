"""docs/03 §9 calibration helpers."""

from __future__ import annotations

import numpy as np
import pytest

from engine.model.calibration import brier, ece, log_loss, reliability_table


def test_perfect_calibration_has_zero_ece() -> None:
    # In each bin the observed rate equals the (single) predicted value.
    probs = np.array([0.25] * 4 + [0.75] * 4)
    outcomes = np.array([1, 0, 0, 0, 1, 1, 1, 0], dtype=float)
    assert ece(probs, outcomes) == pytest.approx(0.0, abs=1e-12)


def test_ece_hand_computed() -> None:
    # bin 0.6-0.7: p=0.65 x2, outcomes 1,1 -> |0.65-1|=0.35, weight 2/4
    # bin 0.3-0.4: p=0.35 x2, outcomes 1,0 -> |0.35-0.5|=0.15, weight 2/4
    probs = np.array([0.65, 0.65, 0.35, 0.35])
    outcomes = np.array([1, 1, 1, 0], dtype=float)
    assert ece(probs, outcomes) == pytest.approx(0.5 * 0.35 + 0.5 * 0.15)


def test_reliability_table_bins_and_edges() -> None:
    probs = np.array([0.0, 0.05, 0.1, 0.999, 1.0])
    outcomes = np.array([0, 0, 1, 1, 1], dtype=float)
    t = reliability_table(probs, outcomes, bins=10)
    assert len(t) == 10
    assert (t[0].count, t[1].count, t[9].count) == (2, 1, 2)  # 1.0 lands in the top bin
    assert t[0].mean_predicted == pytest.approx(0.025) and t[0].observed_rate == 0.0
    assert t[5].count == 0 and t[5].mean_predicted is None
    assert sum(b.count for b in t) == 5


def test_log_loss_and_brier() -> None:
    p = np.array([0.8, 0.3])
    o = np.array([1.0, 0.0])
    assert log_loss(p, o) == pytest.approx(-(np.log(0.8) + np.log(0.7)) / 2)
    assert brier(p, o) == pytest.approx((0.04 + 0.09) / 2)


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        ece(np.array([1.2]), np.array([1.0]))
    with pytest.raises(ValueError):
        ece(np.array([0.5]), np.array([0.5]))
    with pytest.raises(ValueError):
        ece(np.array([]), np.array([]))
