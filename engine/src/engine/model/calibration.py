"""ECE and reliability tables (docs/03 §9).

Equal-width bins on [0, 1]; a probability of exactly 1.0 falls in the top bin.
For half lines the outcome is 1 for `win`, else 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
DEFAULT_BINS = 10


@dataclass(frozen=True)
class ReliabilityBin:
    lo: float
    hi: float
    count: int
    mean_predicted: float | None  # None for an empty bin
    observed_rate: float | None


def _bin_index(probs: FloatArray, bins: int) -> npt.NDArray[np.int64]:
    return np.minimum((probs * bins).astype(np.int64), bins - 1)


def _validate(probs: FloatArray, outcomes: FloatArray) -> None:
    if probs.shape != outcomes.shape:
        raise ValueError("probs and outcomes must have the same shape")
    if np.any((probs < 0) | (probs > 1)):
        raise ValueError("probabilities must lie in [0, 1]")
    if np.any((outcomes != 0) & (outcomes != 1)):
        raise ValueError("outcomes must be 0 or 1")


def reliability_table(
    probs: FloatArray, outcomes: FloatArray, bins: int = DEFAULT_BINS
) -> list[ReliabilityBin]:
    p = np.asarray(probs, dtype=np.float64)
    o = np.asarray(outcomes, dtype=np.float64)
    _validate(p, o)
    idx = _bin_index(p, bins)
    counts = np.bincount(idx, minlength=bins)
    sum_p = np.bincount(idx, weights=p, minlength=bins)
    sum_o = np.bincount(idx, weights=o, minlength=bins)
    table = []
    for b in range(bins):
        n = int(counts[b])
        table.append(
            ReliabilityBin(
                lo=b / bins,
                hi=(b + 1) / bins,
                count=n,
                mean_predicted=float(sum_p[b] / n) if n else None,
                observed_rate=float(sum_o[b] / n) if n else None,
            )
        )
    return table


def ece(probs: FloatArray, outcomes: FloatArray, bins: int = DEFAULT_BINS) -> float:
    """Σ_b (n_b / N) · |mean predicted_b − observed rate_b| over non-empty bins."""
    table = reliability_table(probs, outcomes, bins)
    total = sum(b.count for b in table)
    if total == 0:
        raise ValueError("ece of an empty sample")
    return float(
        sum(
            b.count / total * abs(b.mean_predicted - b.observed_rate)
            for b in table
            if b.mean_predicted is not None and b.observed_rate is not None
        )
    )


def log_loss(probs: FloatArray, outcomes: FloatArray, eps: float = 1e-15) -> float:
    """Mean binary log loss (natural log); probabilities clipped to [eps, 1−eps]."""
    p = np.clip(np.asarray(probs, dtype=np.float64), eps, 1 - eps)
    o = np.asarray(outcomes, dtype=np.float64)
    _validate(np.asarray(probs, dtype=np.float64), o)
    return float(-np.mean(o * np.log(p) + (1 - o) * np.log(1 - p)))


def brier(probs: FloatArray, outcomes: FloatArray) -> float:
    p = np.asarray(probs, dtype=np.float64)
    o = np.asarray(outcomes, dtype=np.float64)
    _validate(p, o)
    return float(np.mean((p - o) ** 2))
