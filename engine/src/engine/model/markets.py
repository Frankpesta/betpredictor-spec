"""Market settlement maths — the single source of truth (docs/03 §8).

The same code prices a selection from a score matrix and settles a real bet
(`settle/` imports `settle` and `result_multiplier` from here). Both paths go
through `_results_grid`, which settles a selection for an array of scorelines.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

Market = Literal["OU", "AH"]
Selection = Literal["over", "under", "home", "away"]
Result = Literal["win", "half_win", "push", "half_loss", "loss"]
LineKind = Literal["half", "whole", "quarter"]

RESULTS: tuple[Result, ...] = ("win", "half_win", "push", "half_loss", "loss")
_SELECTIONS: dict[str, tuple[str, ...]] = {"OU": ("over", "under"), "AH": ("home", "away")}
_TOL = 1e-9

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]


def result_multiplier(result: Result, odds: float) -> float:
    """Return per unit stake at decimal odds (docs/03 §8.1)."""
    if result == "win":
        return odds
    if result == "half_win":
        return (1.0 + odds) / 2.0
    if result == "push":
        return 1.0
    if result == "half_loss":
        return 0.5
    if result == "loss":
        return 0.0
    raise ValueError(f"unknown result {result!r}")


def classify_line(line: float) -> LineKind:
    """docs/03 §8.2: fractional part of |line| decides the kind."""
    f = abs(line) % 1.0
    if abs(f - 0.5) < _TOL:
        return "half"
    if f < _TOL or abs(f - 1.0) < _TOL:
        return "whole"
    if abs(f - 0.25) < _TOL or abs(f - 0.75) < _TOL:
        return "quarter"
    raise ValueError(f"unsupported line {line!r}")


def _check(market: str, selection: str) -> None:
    if selection not in _SELECTIONS.get(market, ()):
        raise ValueError(f"invalid market/selection {market!r}/{selection!r}")


def _margin(market: str, selection: str, line: float, home: IntArray, away: IntArray) -> FloatArray:
    """docs/03 §8.3 margin m for a single (non-quarter) line."""
    total = (home + away).astype(np.float64)
    diff = (home - away).astype(np.float64)
    if selection == "over":
        return total - line
    if selection == "under":
        return line - total
    if selection == "home":
        return diff + line
    return -diff - line  # away, with the home-perspective line


def _sign(m: FloatArray) -> IntArray:
    """+1 win, 0 push, -1 loss, with tolerance (never exact float equality)."""
    return np.where(m > _TOL, 1, np.where(m < -_TOL, -1, 0)).astype(np.int64)


# Result codes index into RESULTS.
_WIN, _HALF_WIN, _PUSH, _HALF_LOSS, _LOSS = range(5)


def _results_grid(
    market: str, selection: str, line: float, home: IntArray, away: IntArray
) -> IntArray:
    """Result code (index into RESULTS) for each scoreline."""
    _check(market, selection)
    if classify_line(line) != "quarter":
        s = _sign(_margin(market, selection, line, home, away))
        return np.where(s > 0, _WIN, np.where(s < 0, _LOSS, _PUSH)).astype(np.int64)

    a = _sign(_margin(market, selection, line - 0.25, home, away))
    b = _sign(_margin(market, selection, line + 0.25, home, away))
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    if np.any((lo == 0) & (hi == 0)):
        raise ValueError(f"push/push on quarter line {line}: impossible")
    if np.any((lo == -1) & (hi == 1)):
        raise ValueError(f"win/loss on quarter line {line}: impossible for adjacent lines")
    out = np.full(a.shape, _LOSS, dtype=np.int64)
    out[(lo == 1) & (hi == 1)] = _WIN
    out[(lo == 0) & (hi == 1)] = _HALF_WIN
    out[(lo == -1) & (hi == 0)] = _HALF_LOSS
    return out


def settle(
    market: Market, selection: Selection, line: float, home_goals: int, away_goals: int
) -> Result:
    """Settle one bet on a final score (docs/03 §8.3-8.4)."""
    if home_goals < 0 or away_goals < 0:
        raise ValueError("goals must be non-negative")
    code = _results_grid(
        market,
        selection,
        line,
        np.array([home_goals], dtype=np.int64),
        np.array([away_goals], dtype=np.int64),
    )[0]
    return RESULTS[int(code)]


@dataclass(frozen=True)
class OutcomeProbs:
    p_win: float
    p_half_win: float
    p_push: float
    p_half_loss: float
    p_loss: float

    def expected_multiplier(self, odds: float) -> float:
        """docs/03 §8.5: E[return per unit stake]."""
        return (
            self.p_win * odds
            + self.p_half_win * (1.0 + odds) / 2.0
            + self.p_push
            + self.p_half_loss * 0.5
        )

    def edge(self, odds: float) -> float:
        return self.expected_multiplier(odds) - 1.0


def price_selection(
    matrix: FloatArray, market: Market, selection: Selection, line: float
) -> OutcomeProbs:
    """Sum score-matrix cell probabilities per settlement result (docs/03 §8.5)."""
    n_home, n_away = matrix.shape
    home, away = np.meshgrid(
        np.arange(n_home, dtype=np.int64), np.arange(n_away, dtype=np.int64), indexing="ij"
    )
    codes = _results_grid(market, selection, line, home, away)
    sums = np.bincount(codes.ravel(), weights=matrix.ravel(), minlength=len(RESULTS))
    return OutcomeProbs(*(float(v) for v in sums))
