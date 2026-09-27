"""Score matrix P[x][y] = τ(x,y)·Poisson(x;λ)·Poisson(y;μ), normalised (docs/03 §6)."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from scipy.stats import poisson

FloatArray = npt.NDArray[np.float64]

# docs/03 §6: truncated tail mass above this means λ or μ is absurd.
MAX_TAIL_MASS = 1e-4


class AbsurdRatesError(ValueError):
    """λ/μ put too much probability beyond max_goals; something upstream is wrong."""


def tau_matrix(lam: float, mu: float, rho: float, max_goals: int) -> FloatArray:
    t = np.ones((max_goals + 1, max_goals + 1), dtype=np.float64)
    t[0, 0] = 1.0 - lam * mu * rho
    t[0, 1] = 1.0 + lam * rho
    t[1, 0] = 1.0 + mu * rho
    t[1, 1] = 1.0 - rho
    return t


def score_matrix(lam: float, mu: float, rho: float, max_goals: int) -> FloatArray:
    if not (lam > 0 and mu > 0):
        raise AbsurdRatesError(f"rates must be positive, got λ={lam}, μ={mu}")
    tail = 1.0 - float(poisson.cdf(max_goals, lam)) * float(poisson.cdf(max_goals, mu))
    if tail > MAX_TAIL_MASS:
        raise AbsurdRatesError(f"tail mass {tail:.2e} > {MAX_TAIL_MASS} for λ={lam}, μ={mu}")
    goals = np.arange(max_goals + 1)
    p = np.outer(poisson.pmf(goals, lam), poisson.pmf(goals, mu)) * tau_matrix(
        lam, mu, rho, max_goals
    )
    if np.any(p < 0):
        raise AbsurdRatesError(f"negative cell probability (ρ={rho}, λ={lam}, μ={mu})")
    out: FloatArray = p / p.sum()
    return out


def to_json_list(p: FloatArray) -> list[list[float]]:
    return [[float(v) for v in row] for row in p]
