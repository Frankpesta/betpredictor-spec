"""Dixon-Coles goals model with time decay and ridge penalty (docs/03 §1-3, §7).

Pure: numpy arrays in, dataclasses out. One fit per league.

    log λ = γ + h·(1 − n) + att[i] + def[j]    (home i vs away j; n = 1 at a neutral venue)
    log μ = γ             + att[j] + def[i]

Neutral venues (docs/09 §3) only occur for internationals; club matches have n = 0.

Identifiability: Σatt = Σdef = 0, by optimising n−1 free values and setting the
last to minus their sum. The same core (with `use_tau=False`, ρ fixed at 0) fits
the xG model in `xg_blend.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

# docs/03 §3: objective returned when any τ ≤ 0 (guards the optimiser).
TAU_GUARD_OBJECTIVE = 1e12
# docs/03 §3 initial values, and the single retry's overrides.
INIT_HOME_ADV = 0.25
INIT_RHO = 0.0
RETRY_HOME_ADV = 0.1
RETRY_RHO = -0.05


class ModelFitError(RuntimeError):
    """The optimiser did not converge (after the retry). Never use such a fit."""


@dataclass(frozen=True)
class TrainingData:
    """Matches of one league; `home_goals`/`away_goals` may be non-integer (xG)."""

    teams: tuple[int, ...]  # team ids; position = index used below
    home_idx: IntArray
    away_idx: IntArray
    home_goals: FloatArray
    away_goals: FloatArray
    weights: FloatArray
    neutral: FloatArray  # 1.0 at a neutral venue (no home advantage), else 0.0

    @property
    def n_teams(self) -> int:
        return len(self.teams)

    @property
    def n_matches(self) -> int:
        return int(self.home_idx.shape[0])


def build_training_data(
    home_team: IntArray,
    away_team: IntArray,
    home_goals: FloatArray,
    away_goals: FloatArray,
    days_before: FloatArray,
    xi_per_day: float,
    neutral: FloatArray | None = None,
) -> TrainingData:
    """Index teams and compute time-decay weights w = exp(−ξ·t) (docs/03 §3)."""
    if np.any(days_before < 0):
        raise ValueError("training match after the fit date")
    teams = tuple(int(t) for t in np.unique(np.concatenate([home_team, away_team])))
    index = {t: i for i, t in enumerate(teams)}
    return TrainingData(
        teams=teams,
        home_idx=np.array([index[int(t)] for t in home_team], dtype=np.int64),
        away_idx=np.array([index[int(t)] for t in away_team], dtype=np.int64),
        home_goals=np.asarray(home_goals, dtype=np.float64),
        away_goals=np.asarray(away_goals, dtype=np.float64),
        weights=np.exp(-xi_per_day * np.asarray(days_before, dtype=np.float64)),
        neutral=(
            np.zeros(len(home_team), dtype=np.float64)
            if neutral is None
            else np.asarray(neutral, dtype=np.float64)
        ),
    )


@dataclass(frozen=True)
class DCParams:
    teams: tuple[int, ...]
    att: FloatArray
    defence: FloatArray
    gamma: float
    home_adv: float
    rho: float
    _index: dict[int, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_index", {t: i for i, t in enumerate(self.teams)})

    def has_team(self, team: int) -> bool:
        return team in self._index

    def log_rates(
        self, home_team: int, away_team: int, neutral: bool = False
    ) -> tuple[float, float]:
        i, j = self._index[home_team], self._index[away_team]
        h = 0.0 if neutral else self.home_adv
        log_lam = self.gamma + h + self.att[i] + self.defence[j]
        log_mu = self.gamma + self.att[j] + self.defence[i]
        return float(log_lam), float(log_mu)

    def rates(self, home_team: int, away_team: int, neutral: bool = False) -> tuple[float, float]:
        log_lam, log_mu = self.log_rates(home_team, away_team, neutral)
        return float(np.exp(log_lam)), float(np.exp(log_mu))

    def to_json_dict(self) -> dict[str, object]:
        return {
            "teams": list(self.teams),
            "att": [float(a) for a in self.att],
            "def": [float(d) for d in self.defence],
            "gamma": self.gamma,
            "home_adv": self.home_adv,
            "rho": self.rho,
        }


@dataclass(frozen=True)
class FitResult:
    params: DCParams
    converged: bool
    neg_log_lik: float  # −LL at the optimum (without the ridge term)
    objective: float
    n_matches: int
    n_iter: int
    message: str
    retried: bool
    matches_per_team: dict[int, int]

    def low_confidence(self, min_team_matches: int) -> set[int]:
        """docs/03 §7: teams with fewer training matches than the threshold."""
        return {t for t, n in self.matches_per_team.items() if n < min_team_matches}


# ---------------------------------------------------------------------------
# Parameter vector: [γ, h, (ρ), att_free (n−1), def_free (n−1)]
# ---------------------------------------------------------------------------


def _unpack(
    theta: FloatArray, n: int, use_tau: bool
) -> tuple[float, float, float, FloatArray, FloatArray]:
    gamma, h = float(theta[0]), float(theta[1])
    k = 2
    rho = 0.0
    if use_tau:
        rho = float(theta[2])
        k = 3
    att_free = theta[k : k + n - 1]
    def_free = theta[k + n - 1 : k + 2 * (n - 1)]
    att = np.append(att_free, -att_free.sum())
    defence = np.append(def_free, -def_free.sum())
    return gamma, h, rho, att, defence


def _tau_terms(
    x: FloatArray, y: FloatArray, lam: FloatArray, mu: FloatArray, rho: float
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    """τ and d log τ / d(log λ, log μ, ρ) per match (docs/03 §2)."""
    tau = np.ones_like(lam)
    d_loglam = np.zeros_like(lam)
    d_logmu = np.zeros_like(lam)
    d_rho = np.zeros_like(lam)

    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)

    lm = lam[m00] * mu[m00]
    tau[m00] = 1.0 - lm * rho
    d_loglam[m00] = -lm * rho / tau[m00]
    d_logmu[m00] = -lm * rho / tau[m00]
    d_rho[m00] = -lm / tau[m00]

    tau[m01] = 1.0 + lam[m01] * rho
    d_loglam[m01] = lam[m01] * rho / tau[m01]
    d_rho[m01] = lam[m01] / tau[m01]

    tau[m10] = 1.0 + mu[m10] * rho
    d_logmu[m10] = mu[m10] * rho / tau[m10]
    d_rho[m10] = mu[m10] / tau[m10]

    tau[m11] = 1.0 - rho
    d_rho[m11] = -1.0 / tau[m11]
    return tau, d_loglam, d_logmu, d_rho


def objective_and_grad(
    theta: FloatArray, data: TrainingData, ridge: float, use_tau: bool
) -> tuple[float, FloatArray]:
    """−LL + ridge·(Σatt² + Σdef²) and its gradient w.r.t. theta (vectorised)."""
    n = data.n_teams
    gamma, h, rho, att, defence = _unpack(theta, n, use_tau)
    hi, ai = data.home_idx, data.away_idx
    x, y, w = data.home_goals, data.away_goals, data.weights

    home_on = 1.0 - data.neutral
    log_lam = gamma + h * home_on + att[hi] + defence[ai]
    log_mu = gamma + att[ai] + defence[hi]
    lam, mu = np.exp(log_lam), np.exp(log_mu)

    ll_terms = x * log_lam - lam + y * log_mu - mu
    g_lam = x - lam  # d LL_k / d log λ_k (before τ and weight)
    g_mu = y - mu
    g_rho_total = 0.0
    if use_tau:
        tau, t_lam, t_mu, t_rho = _tau_terms(x, y, lam, mu, rho)
        if np.any(tau <= 0):
            return TAU_GUARD_OBJECTIVE, np.zeros_like(theta)
        ll_terms = ll_terms + np.log(tau)
        g_lam = g_lam + t_lam
        g_mu = g_mu + t_mu
        g_rho_total = float(np.sum(w * t_rho))

    ll = float(np.sum(w * ll_terms))
    penalty = ridge * float(np.sum(att**2) + np.sum(defence**2))
    obj = -ll + penalty

    wl, wm = w * g_lam, w * g_mu
    d_gamma = float(np.sum(wl) + np.sum(wm))
    d_h = float(np.sum(wl * home_on))
    d_att = np.bincount(hi, weights=wl, minlength=n) + np.bincount(ai, weights=wm, minlength=n)
    d_def = np.bincount(ai, weights=wl, minlength=n) + np.bincount(hi, weights=wm, minlength=n)
    # objective = −LL + ridge: flip sign, add penalty gradient on the full vectors
    g_att_full = -d_att + 2.0 * ridge * att
    g_def_full = -d_def + 2.0 * ridge * defence
    # chain rule through att_last = −Σ att_free
    g_att_free = g_att_full[:-1] - g_att_full[-1]
    g_def_free = g_def_full[:-1] - g_def_full[-1]

    head = [-d_gamma, -d_h] + ([-g_rho_total] if use_tau else [])
    grad = np.concatenate([np.array(head, dtype=np.float64), g_att_free, g_def_free])
    return obj, grad


def _initial_theta(data: TrainingData, use_tau: bool, home_adv: float, rho: float) -> FloatArray:
    total = float(np.sum(data.home_goals) + np.sum(data.away_goals))
    mean_per_team = total / (2 * data.n_matches)
    head = [np.log(mean_per_team), home_adv] + ([rho] if use_tau else [])
    return np.concatenate(
        [np.array(head, dtype=np.float64), np.zeros(2 * (data.n_teams - 1), dtype=np.float64)]
    )


def fit(
    data: TrainingData,
    *,
    ridge: float,
    rho_bounds: tuple[float, float] = (-0.2, 0.2),
    use_tau: bool = True,
) -> FitResult:
    """Fit by L-BFGS-B; retry once from (h=0.1, ρ=−0.05); raise if still unconverged."""
    if data.n_matches == 0 or data.n_teams < 2:
        raise ModelFitError("not enough training data")
    if use_tau and np.any((data.home_goals % 1 != 0) | (data.away_goals % 1 != 0)):
        raise ValueError("the Dixon-Coles τ needs integer goals; use use_tau=False for xG")

    n_free = 2 * (data.n_teams - 1)
    bounds: list[tuple[float | None, float | None]] = [(None, None), (None, None)]
    if use_tau:
        bounds.append(rho_bounds)
    bounds += [(None, None)] * n_free

    attempts = [(INIT_HOME_ADV, INIT_RHO), (RETRY_HOME_ADV, RETRY_RHO)]
    res = None
    retried = False
    for attempt, (h0, rho0) in enumerate(attempts):
        retried = attempt > 0
        theta0 = _initial_theta(data, use_tau, h0, rho0)
        res = minimize(
            objective_and_grad,
            theta0,
            args=(data, ridge, use_tau),
            jac=True,
            method="L-BFGS-B",
            bounds=bounds,
        )
        if res.success:
            break
    assert res is not None
    if not res.success:
        raise ModelFitError(f"optimiser did not converge after retry: {res.message}")

    gamma, h, rho, att, defence = _unpack(
        np.asarray(res.x, dtype=np.float64), data.n_teams, use_tau
    )
    penalty = ridge * float(np.sum(att**2) + np.sum(defence**2))
    counts = np.bincount(data.home_idx, minlength=data.n_teams) + np.bincount(
        data.away_idx, minlength=data.n_teams
    )
    return FitResult(
        params=DCParams(
            teams=data.teams, att=att, defence=defence, gamma=gamma, home_adv=h, rho=rho
        ),
        converged=True,
        neg_log_lik=float(res.fun) - penalty,
        objective=float(res.fun),
        n_matches=data.n_matches,
        n_iter=int(res.nit),
        message=str(res.message),
        retried=retried,
        matches_per_team={t: int(c) for t, c in zip(data.teams, counts, strict=True)},
    )
