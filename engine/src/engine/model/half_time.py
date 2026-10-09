"""Half-time model: split the full-time rates into two halves (docs/10 §2). Pure.

Each half's expected goals are a league share of the full-time rate (first half
s·λ, second half (1 − s)·λ, separately for home and away); the halves are independent
and each gets its own Dixon–Coles low-score correction ρ, fitted by maximum likelihood.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize_scalar

from engine.model.league import LeagueMatches, LeagueModel, training_window
from engine.model.score_matrix import FloatArray, score_matrix

Half = Literal[1, 2]
ArrayLike = npt.NDArray[np.float64]


@dataclass(frozen=True)
class HalfTimeParams:
    share_home: float  # first-half share of the home team's goals
    share_away: float
    rho_1h: float
    rho_2h: float
    n_matches: int  # training matches with half-time goals

    def to_json(self) -> dict[str, float | int]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, float | int]) -> HalfTimeParams:
        return cls(
            float(d["share_home"]),
            float(d["share_away"]),
            float(d["rho_1h"]),
            float(d["rho_2h"]),
            int(d["n_matches"]),
        )


def fit_shares(
    ft_home: ArrayLike, ft_away: ArrayLike, ht_home: ArrayLike, ht_away: ArrayLike
) -> tuple[float, float]:
    """docs/10 §2: Σ first-half goals / Σ full-time goals, home and away (no decay)."""
    if np.any(ht_home > ft_home) or np.any(ht_away > ft_away):
        raise ValueError("half-time goals above full-time goals")
    th, ta = float(ft_home.sum()), float(ft_away.sum())
    if th <= 0 or ta <= 0:
        raise ValueError("no full-time goals to split")
    return float(ht_home.sum()) / th, float(ht_away.sum()) / ta


def half_rates(lam: float, mu: float, p: HalfTimeParams, half: Half) -> tuple[float, float]:
    if half == 1:
        return p.share_home * lam, p.share_away * mu
    return (1.0 - p.share_home) * lam, (1.0 - p.share_away) * mu


def _log_tau(rho: float, lam: ArrayLike, mu: ArrayLike, x: ArrayLike, y: ArrayLike) -> float | None:
    """Σ log τ(x, y) — the only ρ-dependent part of the Dixon–Coles likelihood (τ keeps
    each score matrix summing to 1). None when some τ ≤ 0 (ρ infeasible)."""
    tau = np.ones_like(lam)
    m00, m01, m10, m11 = (
        (x == 0) & (y == 0),
        (x == 0) & (y == 1),
        (x == 1) & (y == 0),
        (x == 1) & (y == 1),
    )
    tau[m00] = 1.0 - lam[m00] * mu[m00] * rho
    tau[m01] = 1.0 + lam[m01] * rho
    tau[m10] = 1.0 + mu[m10] * rho
    tau[m11] = 1.0 - rho
    if np.any(tau <= 0):
        return None
    return float(np.log(tau).sum())


def fit_rho(
    lam: ArrayLike, mu: ArrayLike, x: ArrayLike, y: ArrayLike, bounds: tuple[float, float]
) -> float:
    """Maximum-likelihood ρ for given rates and observed scores, within `bounds`."""

    def neg(rho: float) -> float:
        ll = _log_tau(rho, lam, mu, x, y)
        return np.inf if ll is None else -ll

    res = minimize_scalar(neg, bounds=bounds, method="bounded", options={"xatol": 1e-6})
    if not res.success or not np.isfinite(res.fun):
        raise ValueError(f"half-time rho fit failed: {res.message}")
    return float(res.x)


def fit_half_time(
    lam: ArrayLike,
    mu: ArrayLike,
    ft_home: ArrayLike,
    ft_away: ArrayLike,
    ht_home: ArrayLike,
    ht_away: ArrayLike,
    rho_bounds: tuple[float, float],
) -> HalfTimeParams:
    """docs/10 §2 on one training window. `lam`/`mu`: the full-time model's rates for the
    same matches; goals arrays are full-time and first-half goals."""
    sh, sa = fit_shares(ft_home, ft_away, ht_home, ht_away)
    rho1 = fit_rho(sh * lam, sa * mu, ht_home, ht_away, rho_bounds)
    rho2 = fit_rho(
        (1.0 - sh) * lam, (1.0 - sa) * mu, ft_home - ht_home, ft_away - ht_away, rho_bounds
    )
    return HalfTimeParams(sh, sa, rho1, rho2, int(lam.shape[0]))


def half_matrix(lam: float, mu: float, p: HalfTimeParams, half: Half, max_goals: int) -> FloatArray:
    """Score matrix of one half: P[home goals in the half][away goals in the half]."""
    lh, mh = half_rates(lam, mu, p, half)
    rho = p.rho_1h if half == 1 else p.rho_2h
    return score_matrix(lh, mh, rho, max_goals)


def training_rows(
    model: LeagueModel,
    hist: LeagueMatches,
    ht_home: FloatArray,
    ht_away: FloatArray,
    fit_time: float,
    train_seasons_back: int,
) -> npt.NDArray[np.int64]:
    """Indices of `hist` used to fit the half-time parameters: the full-time model's
    training window, half-time goals known, both teams in the model (docs/10 §2)."""
    ws, we = training_window(fit_time, train_seasons_back)
    known = ~np.isnan(ht_home) & ~np.isnan(ht_away)
    idx = np.flatnonzero((hist.kickoff >= ws) & (hist.kickoff < we) & known)
    assert idx.size == 0 or float(hist.kickoff[idx].max()) < fit_time, "training data leaks"
    keep = [
        i for i in idx if model.has_team(int(hist.home[i])) and model.has_team(int(hist.away[i]))
    ]
    return np.array(keep, dtype=np.int64)


def fit_for_model(
    model: LeagueModel,
    hist: LeagueMatches,
    ht_home: FloatArray,
    ht_away: FloatArray,
    fit_time: float,
    train_seasons_back: int,
    rho_bounds: tuple[float, float],
    blend: float,
    min_matches: int,
) -> HalfTimeParams | None:
    """docs/10 §2 for a fitted full-time model; None when fewer than `min_matches` rows."""
    rows = training_rows(model, hist, ht_home, ht_away, fit_time, train_seasons_back)
    if rows.size < min_matches:
        return None
    rates = np.array([model.rates(int(hist.home[i]), int(hist.away[i]), blend) for i in rows])
    return fit_half_time(
        rates[:, 0],
        rates[:, 1],
        hist.home_goals[rows],
        hist.away_goals[rows],
        ht_home[rows],
        ht_away[rows],
        rho_bounds,
    )
