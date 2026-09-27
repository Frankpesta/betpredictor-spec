"""docs/03 §8.8 parameter recovery on a simulated 20-team league."""

from __future__ import annotations

import numpy as np

from engine.model.dixon_coles import TrainingData, fit
from engine.model.score_matrix import score_matrix
from engine.model.xg_blend import fit_xg

N_TEAMS = 20
GAMMA, HOME, RHO = 0.15, 0.25, -0.08
MAX_GOALS = 15


def _simulate(seed: int) -> tuple[TrainingData, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    att = rng.normal(0, 0.25, N_TEAMS)
    att -= att.mean()
    dfn = rng.normal(0, 0.25, N_TEAMS)
    dfn -= dfn.mean()
    fixtures = [(i, j) for i in range(N_TEAMS) for j in range(N_TEAMS) if i != j] * 3
    hs, as_, hg, ag = [], [], [], []
    cells = (MAX_GOALS + 1) ** 2
    for i, j in fixtures:
        lam = np.exp(GAMMA + HOME + att[i] + dfn[j])
        mu = np.exp(GAMMA + att[j] + dfn[i])
        p = score_matrix(lam, mu, RHO, MAX_GOALS).ravel()
        k = rng.choice(cells, p=p)
        hs.append(i)
        as_.append(j)
        hg.append(k // (MAX_GOALS + 1))
        ag.append(k % (MAX_GOALS + 1))
    n = len(fixtures)
    data = TrainingData(
        teams=tuple(range(N_TEAMS)),
        home_idx=np.array(hs, dtype=np.int64),
        away_idx=np.array(as_, dtype=np.int64),
        home_goals=np.array(hg, dtype=np.float64),
        away_goals=np.array(ag, dtype=np.float64),
        weights=np.ones(n),  # xi_per_day = 0
        neutral=np.zeros(n),
    )
    return data, att, dfn


def test_parameter_recovery() -> None:
    data, att, dfn = _simulate(seed=20260927)
    res = fit(data, ridge=0.0, rho_bounds=(-0.2, 0.2))
    p = res.params
    assert res.converged and res.n_matches == 3 * N_TEAMS * (N_TEAMS - 1)
    assert np.corrcoef(p.att, att)[0, 1] > 0.9
    assert np.corrcoef(p.defence, dfn)[0, 1] > 0.9
    assert abs(p.home_adv - HOME) < 0.08
    assert abs(p.rho - RHO) < 0.07
    assert abs(p.att.sum()) < 1e-9 and abs(p.defence.sum()) < 1e-9


def test_xg_model_recovers_structure_from_expected_goals() -> None:
    """Fitting the true λ/μ as 'xG' should recover the parameters almost exactly."""
    data, att, dfn = _simulate(seed=7)
    lam = np.exp(GAMMA + HOME + att[data.home_idx] + dfn[data.away_idx])
    mu = np.exp(GAMMA + att[data.away_idx] + dfn[data.home_idx])
    xg = TrainingData(data.teams, data.home_idx, data.away_idx, lam, mu, data.weights, data.neutral)
    p = fit_xg(xg, ridge=0.0).params
    np.testing.assert_allclose(p.att, att, atol=1e-3)
    np.testing.assert_allclose(p.defence, dfn, atol=1e-3)
    assert abs(p.home_adv - HOME) < 1e-3 and p.rho == 0.0


def test_neutral_venues_recover_home_advantage() -> None:
    """Half the matches at neutral venues: h is still recovered, and neutral rates drop h."""
    rng = np.random.default_rng(11)
    data, att, dfn = _simulate(seed=11)
    neutral = (rng.uniform(size=data.n_matches) < 0.5).astype(float)
    # Re-draw scores so neutral matches really have no home advantage.
    hg, ag = [], []
    for i, j, n in zip(data.home_idx, data.away_idx, neutral, strict=True):
        lam = np.exp(GAMMA + HOME * (1 - n) + att[i] + dfn[j])
        mu = np.exp(GAMMA + att[j] + dfn[i])
        k = rng.choice((MAX_GOALS + 1) ** 2, p=score_matrix(lam, mu, RHO, MAX_GOALS).ravel())
        hg.append(k // (MAX_GOALS + 1))
        ag.append(k % (MAX_GOALS + 1))
    d = TrainingData(
        data.teams, data.home_idx, data.away_idx, np.array(hg, float), np.array(ag, float),
        data.weights, neutral,
    )  # fmt: skip
    p = fit(d, ridge=0.0).params
    assert abs(p.home_adv - HOME) < 0.1
    lam_home, _ = p.rates(0, 1)
    lam_neutral, _ = p.rates(0, 1, neutral=True)
    assert abs(np.log(lam_home) - np.log(lam_neutral) - p.home_adv) < 1e-12
