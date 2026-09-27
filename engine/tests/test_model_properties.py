"""docs/03 §8.7 property tests + gradient check of the analytic DC gradient."""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from scipy.optimize import check_grad
from scipy.stats import poisson

from engine.model.dixon_coles import TrainingData, objective_and_grad
from engine.model.markets import RESULTS, price_selection
from engine.model.score_matrix import AbsurdRatesError, score_matrix

MAX_GOALS = 15
RATES = [(0.4, 0.3), (1.0, 1.0), (1.45, 1.12), (2.6, 0.6), (3.2, 0.9), (4.5, 0.4)]
RHOS = [-0.15, -0.05, 0.0, 0.1]
HALF_LINES_OU = [0.5, 1.5, 2.5, 3.5, 4.5]
HALF_LINES_AH = [-2.5, -1.5, -0.5, 0.5, 1.5, 2.5]
ALL_LINES_OU = [1.5, 2.0, 2.25, 2.5, 2.75, 3.0, 3.25]
ALL_LINES_AH = [-1.75, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0]


@pytest.mark.parametrize(("lam", "mu"), RATES)
@pytest.mark.parametrize("rho", RHOS)
def test_matrix_sums_to_one_nonnegative(lam: float, mu: float, rho: float) -> None:
    p = score_matrix(lam, mu, rho, MAX_GOALS)
    assert p.shape == (MAX_GOALS + 1, MAX_GOALS + 1)
    assert abs(p.sum() - 1.0) < 1e-9
    assert (p >= 0).all()


@pytest.mark.parametrize(("lam", "mu"), RATES)
def test_rho_zero_is_outer_product_of_poissons(lam: float, mu: float) -> None:
    g = np.arange(MAX_GOALS + 1)
    outer = np.outer(poisson.pmf(g, lam), poisson.pmf(g, mu))
    outer /= outer.sum()
    np.testing.assert_allclose(score_matrix(lam, mu, 0.0, MAX_GOALS), outer, atol=1e-15)


@pytest.mark.parametrize(("lam", "mu"), RATES)
@pytest.mark.parametrize("rho", [-0.1, 0.0])
def test_half_lines_are_complementary_with_no_partial_results(
    lam: float, mu: float, rho: float
) -> None:
    p = score_matrix(lam, mu, rho, MAX_GOALS)
    for market, sels, lines in (
        ("OU", ("over", "under"), HALF_LINES_OU),
        ("AH", ("home", "away"), HALF_LINES_AH),
    ):
        for line in lines:
            a = price_selection(p, market, sels[0], line)  # type: ignore[arg-type]
            b = price_selection(p, market, sels[1], line)  # type: ignore[arg-type]
            assert abs(a.p_win + b.p_win - 1.0) < 1e-9
            for o in (a, b):
                assert o.p_push == o.p_half_win == o.p_half_loss == 0.0


@pytest.mark.parametrize(("lam", "mu"), RATES)
def test_every_priced_selection_sums_to_one(lam: float, mu: float) -> None:
    p = score_matrix(lam, mu, -0.08, MAX_GOALS)
    for market, sels, lines in (
        ("OU", ("over", "under"), ALL_LINES_OU),
        ("AH", ("home", "away"), ALL_LINES_AH),
    ):
        for sel in sels:
            for line in lines:
                o = price_selection(p, market, sel, line)  # type: ignore[arg-type]
                total = sum(getattr(o, f"p_{r}") for r in RESULTS)
                assert abs(total - 1.0) < 1e-9


@pytest.mark.parametrize("mu", [0.5, 1.1, 1.8])
def test_over_25_non_decreasing_in_lambda(mu: float) -> None:
    probs = [
        price_selection(score_matrix(lam, mu, -0.08, MAX_GOALS), "OU", "over", 2.5).p_win
        for lam in np.linspace(0.2, 3.5, 34)
    ]
    assert all(b >= a - 1e-12 for a, b in itertools.pairwise(probs))


def test_absurd_rates_raise() -> None:
    with pytest.raises(AbsurdRatesError):
        score_matrix(6.0, 1.0, 0.0, MAX_GOALS)
    with pytest.raises(AbsurdRatesError):
        score_matrix(0.0, 1.0, 0.0, MAX_GOALS)


# ---- gradient check (docs/03 §3: error < 1e-4) --------------------------------


def _random_data(rng: np.random.Generator, n_teams: int, n: int, xg: bool) -> TrainingData:
    home = rng.integers(0, n_teams, n)
    away = (home + rng.integers(1, n_teams, n)) % n_teams
    if xg:
        hg, ag = rng.gamma(2.0, 0.7, n), rng.gamma(2.0, 0.55, n)
    else:
        hg, ag = rng.poisson(1.5, n).astype(float), rng.poisson(1.1, n).astype(float)
    return TrainingData(
        teams=tuple(range(n_teams)),
        home_idx=home.astype(np.int64),
        away_idx=away.astype(np.int64),
        home_goals=hg,
        away_goals=ag,
        weights=np.exp(-0.0019 * rng.uniform(0, 1000, n)),
        neutral=(rng.uniform(size=n) < 0.3).astype(float),  # exercise the h·(1 − n) term
    )


@pytest.mark.parametrize("use_tau", [True, False])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_analytic_gradient_matches_numeric(use_tau: bool, seed: int) -> None:
    rng = np.random.default_rng(seed)
    n_teams = 8
    data = _random_data(rng, n_teams, 300, xg=not use_tau)
    head = [0.1, 0.25] + ([-0.06] if use_tau else [])
    theta = np.concatenate([head, rng.normal(0, 0.2, 2 * (n_teams - 1))])

    def f(t: np.ndarray) -> float:
        return objective_and_grad(t, data, 0.5, use_tau)[0]

    def g(t: np.ndarray) -> np.ndarray:
        return objective_and_grad(t, data, 0.5, use_tau)[1]

    err = check_grad(f, g, theta)
    scale = max(1.0, float(np.linalg.norm(g(theta))))
    assert err / scale < 1e-4, (err, scale)
