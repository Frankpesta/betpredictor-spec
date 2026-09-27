"""docs/03 §8.6 mandatory settlement table — must pass unchanged."""

from __future__ import annotations

import pytest

from engine.model.markets import classify_line, result_multiplier, settle

# (selection, home-perspective line, score, expected) — docs/03 §8.6, AH
AH_TABLE = [
    ("home", -0.5, "1-0", "win"),
    ("home", -0.5, "0-0", "loss"),
    ("home", -0.5, "1-1", "loss"),
    ("home", +0.5, "0-0", "win"),
    ("home", +0.5, "0-1", "loss"),
    ("home", -1.0, "2-0", "win"),
    ("home", -1.0, "1-0", "push"),
    ("home", -1.0, "0-0", "loss"),
    ("home", -0.25, "1-0", "win"),
    ("home", -0.25, "0-0", "half_loss"),
    ("home", -0.25, "0-1", "loss"),
    ("home", +0.25, "0-0", "half_win"),
    ("home", +0.25, "1-0", "win"),
    ("home", +0.25, "0-1", "loss"),
    ("home", -0.75, "2-0", "win"),
    ("home", -0.75, "1-0", "half_win"),
    ("home", -0.75, "0-0", "loss"),
    ("home", +0.75, "0-0", "win"),
    ("home", +0.75, "0-1", "half_loss"),
    ("home", +0.75, "0-2", "loss"),
    ("home", -1.5, "2-0", "win"),
    ("home", -1.5, "1-0", "loss"),
    ("away", -0.5, "0-0", "win"),
    ("away", -0.5, "1-0", "loss"),
    ("away", -0.75, "0-0", "win"),
    ("away", -0.75, "1-0", "half_loss"),
    ("away", -0.75, "2-0", "loss"),
]

# (selection, line, total goals, expected) — docs/03 §8.6, Over/Under
OU_TABLE = [
    ("over", 2.5, 3, "win"),
    ("over", 2.5, 2, "loss"),
    ("under", 2.5, 2, "win"),
    ("under", 2.5, 3, "loss"),
    ("over", 2.0, 3, "win"),
    ("over", 2.0, 2, "push"),
    ("over", 2.0, 1, "loss"),
    ("over", 2.25, 3, "win"),
    ("over", 2.25, 2, "half_loss"),
    ("over", 2.75, 4, "win"),
    ("over", 2.75, 3, "half_win"),
    ("over", 2.75, 2, "loss"),
    ("under", 2.25, 1, "win"),
    ("under", 2.25, 2, "half_win"),
    ("under", 2.25, 3, "loss"),
    ("under", 2.75, 2, "win"),
    ("under", 2.75, 3, "half_loss"),
]


@pytest.mark.parametrize(("selection", "line", "score", "expected"), AH_TABLE)
def test_ah_table(selection: str, line: float, score: str, expected: str) -> None:
    hg, ag = (int(g) for g in score.split("-"))
    assert settle("AH", selection, line, hg, ag) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(("selection", "line", "total", "expected"), OU_TABLE)
def test_ou_table(selection: str, line: float, total: int, expected: str) -> None:
    # the total is what matters; check two splits of it
    assert settle("OU", selection, line, total, 0) == expected  # type: ignore[arg-type]
    assert settle("OU", selection, line, 0, total) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("result", "expected"),
    [("win", 1.90), ("half_win", 1.45), ("push", 1.0), ("half_loss", 0.5), ("loss", 0.0)],
)
def test_multipliers_at_190(result: str, expected: float) -> None:
    assert result_multiplier(result, 1.90) == pytest.approx(expected, abs=1e-12)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        (2.5, "half"),
        (-0.5, "half"),
        (-1.5, "half"),
        (0.0, "whole"),
        (-1.0, "whole"),
        (3.0, "whole"),
        (-0.25, "quarter"),
        (0.75, "quarter"),
        (-2.75, "quarter"),
        (2.25, "quarter"),
    ],
)
def test_classify_line(line: float, kind: str) -> None:
    assert classify_line(line) == kind


@pytest.mark.parametrize("line", [0.1, -0.3, 2.6])
def test_classify_line_rejects_odd_lines(line: float) -> None:
    with pytest.raises(ValueError):
        classify_line(line)


def test_invalid_selection_rejected() -> None:
    with pytest.raises(ValueError):
        settle("OU", "home", 2.5, 1, 1)
    with pytest.raises(ValueError):
        settle("AH", "over", -0.5, 1, 1)
