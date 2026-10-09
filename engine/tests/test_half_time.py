"""docs/10 — half-time model: shares, ρ fit, half matrices."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.stats import poisson

from engine.model.half_time import (
    HalfTimeParams,
    fit_half_time,
    fit_rho,
    fit_shares,
    half_matrix,
    half_rates,
)

BOUNDS = (-0.2, 0.2)


def test_fit_shares_table() -> None:
    ft_h, ft_a = np.array([2.0, 1.0, 3.0]), np.array([0.0, 2.0, 2.0])
    ht_h, ht_a = np.array([1.0, 0.0, 2.0]), np.array([0.0, 1.0, 1.0])
    assert fit_shares(ft_h, ft_a, ht_h, ht_a) == pytest.approx((3 / 6, 2 / 4))
    with pytest.raises(ValueError, match="above"):
        fit_shares(ft_h, ft_a, ht_h + 5, ht_a)
    with pytest.raises(ValueError, match="no full-time"):
        fit_shares(np.zeros(2), np.zeros(2), np.zeros(2), np.zeros(2))


def test_half_rates_split_the_full_time_rate() -> None:
    p = HalfTimeParams(0.45, 0.4, 0.0, 0.0, 100)
    assert half_rates(2.0, 1.0, p, 1) == pytest.approx((0.9, 0.4))
    assert half_rates(2.0, 1.0, p, 2) == pytest.approx((1.1, 0.6))


def test_half_matrices_sum_to_one_and_convolve_to_full_time_total() -> None:
    p = HalfTimeParams(0.45, 0.45, 0.0, 0.0, 100)
    m1, m2 = half_matrix(1.6, 1.2, p, 1, 15), half_matrix(1.6, 1.2, p, 2, 15)
    assert m1.sum() == pytest.approx(1.0) and m2.sum() == pytest.approx(1.0)
    # totals per half, convolved = Poisson(λ + μ) full-time total (ρ = 0)
    goals = np.add.outer(np.arange(16), np.arange(16)).ravel()  # total goals of each cell
    t1 = np.bincount(goals, weights=m1.ravel())
    t2 = np.bincount(goals, weights=m2.ravel())
    total = np.convolve(t1, t2)[:8]
    assert total == pytest.approx(poisson.pmf(np.arange(8), 2.8), abs=1e-9)


def _simulate(rho: float, n: int, seed: int) -> tuple[np.ndarray, ...]:
    """Half scores from the DC distribution with known ρ (exact sampling from the matrix)."""
    rng = np.random.default_rng(seed)
    lam = rng.uniform(0.4, 1.0, n)
    mu = rng.uniform(0.3, 0.8, n)
    x, y = np.empty(n), np.empty(n)
    p = HalfTimeParams(1.0, 1.0, rho, rho, n)
    for i in range(n):
        mat = half_matrix(float(lam[i]), float(mu[i]), p, 1, 8)
        k = rng.choice(mat.size, p=mat.ravel())
        x[i], y[i] = divmod(k, mat.shape[1])
    return lam, mu, x, y


@pytest.mark.parametrize("rho", [-0.12, 0.0, 0.08])
def test_fit_rho_recovers_simulated_value(rho: float) -> None:
    lam, mu, x, y = _simulate(rho, 4000, seed=7)
    assert fit_rho(lam, mu, x, y, BOUNDS) == pytest.approx(rho, abs=0.05)


def test_fit_rho_stays_in_bounds() -> None:
    lam, mu, x, y = _simulate(-0.2, 500, seed=3)
    got = fit_rho(lam, mu, x, y, (-0.05, 0.05))
    assert -0.05 <= got <= 0.05


def test_fit_half_time_end_to_end_and_json_roundtrip() -> None:
    rng = np.random.default_rng(11)
    n = 3000
    lam, mu = rng.uniform(1.0, 2.2, n), rng.uniform(0.7, 1.6, n)
    ht_h, ht_a = rng.poisson(0.45 * lam), rng.poisson(0.42 * mu)
    sh_h, sh_a = rng.poisson(0.55 * lam), rng.poisson(0.58 * mu)
    ft_h, ft_a = (ht_h + sh_h).astype(float), (ht_a + sh_a).astype(float)
    p = fit_half_time(lam, mu, ft_h, ft_a, ht_h.astype(float), ht_a.astype(float), BOUNDS)
    assert p.share_home == pytest.approx(0.45, abs=0.02)
    assert p.share_away == pytest.approx(0.42, abs=0.02)
    assert abs(p.rho_1h) < 0.08 and abs(p.rho_2h) < 0.08  # independent Poisson: ρ ≈ 0
    assert p.n_matches == n
    assert HalfTimeParams.from_json(p.to_json()) == p


# ---- docs/10 §4: backtest scoring + gate ----
from engine.backtest.half_time import HtTask, price_season, score_market  # noqa: E402
from engine.config import get_settings  # noqa: E402
from engine.db.model_runs import fit_settings  # noqa: E402
from engine.model.league import SECONDS_PER_DAY, LeagueMatches  # noqa: E402


def test_score_market_gate_table() -> None:
    y = np.array([1.0, 0.0] * 500)
    good = np.where(y == 1, 0.7, 0.3)  # informative and reasonably calibrated
    base = np.full(y.size, 0.5)
    sc = score_market(1, "O/U 0.5", good, base, y, max_ece=0.31)
    assert sc.n == 1000 and sc.log_loss < sc.log_loss_base and sc.passed
    assert not score_market(1, "O/U 0.5", good, base, y, max_ece=0.01).passed  # ECE 0.3
    assert not score_market(1, "O/U 0.5", base, good, y, max_ece=1.0).passed  # loses to baseline


def test_score_market_1x2_is_three_way() -> None:
    # two matches: home won, draw
    p = np.array([0.5, 0.3, 0.2, 0.4, 0.4, 0.2])
    y = np.array([1.0, 0.0, 0.0, 0.0, 1.0, 0.0])
    sc = score_market(1, "1X2", p, p, y, max_ece=1.0)
    assert sc.n == 2
    assert sc.log_loss == pytest.approx(-(np.log(0.5) + np.log(0.4)) / 2)


def _synthetic_league(
    n_seasons: int = 4, seed: int = 1
) -> tuple[LeagueMatches, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    teams = np.arange(1, 11)
    att = rng.normal(0, 0.25, 11)
    rows = []
    t0 = 1_500_000_000.0
    k = 0
    for season in range(n_seasons):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                lam, mu = np.exp(0.3 + att[h] - att[a]), np.exp(0.1 + att[a] - att[h])
                hth, hta = rng.poisson(0.45 * lam), rng.poisson(0.45 * mu)
                fth, fta = hth + rng.poisson(0.55 * lam), hta + rng.poisson(0.55 * mu)
                rows.append(
                    (
                        k,
                        t0 + (season * 365 + (k % 90) * 3) * SECONDS_PER_DAY + k,
                        h,
                        a,
                        fth,
                        fta,
                        hth,
                        hta,
                        season,
                    )
                )
                k += 1
    r = np.array(rows, dtype=float)
    r = r[np.argsort(r[:, 1])]
    lm = LeagueMatches(
        match_id=r[:, 0].astype(np.int64),
        kickoff=r[:, 1],
        home=r[:, 2].astype(np.int64),
        away=r[:, 3].astype(np.int64),
        home_goals=r[:, 4],
        away_goals=r[:, 5],
        home_xg=np.full(len(r), np.nan),
        away_xg=np.full(len(r), np.nan),
        neutral=np.zeros(len(r)),
    )
    return lm, r[:, 6], r[:, 7], r[:, 8]


def test_price_season_walk_forward_on_synthetic_league() -> None:
    hist, hth, hta, season = _synthetic_league()
    s = get_settings()
    task = HtTask(
        league="X",
        season="last",
        history=hist,
        ht_home=hth,
        ht_away=hta,
        season_mask=season == season.max(),
        fit=fit_settings(s),
        blend=1.0,
        refit_days=60,
        max_goals=10,
    )
    rows = price_season(task)
    assert rows.params and len(rows.p_model) == len(rows.p_base) == len(rows.outcome)
    shares = [p["share_home"] for p in rows.params]
    assert np.mean(shares) == pytest.approx(0.45, abs=0.03)
    p, y = np.array(rows.p_model), np.array(rows.outcome)
    assert ((p >= 0) & (p <= 1)).all() and set(np.unique(y)) <= {0.0, 1.0}


# ---- docs/10 §3: settlement of half-time markets ----
from engine.model.markets import settle, split_period  # noqa: E402
from engine.settle.settle import Decision, period_score  # noqa: E402
from engine.sportybet.results import first_half_score  # noqa: E402


@pytest.mark.parametrize(
    ("market", "expected"),
    [
        ("OU", ("OU", "FT")),
        ("OU_1H", ("OU", "1H")),
        ("OU_HOME_2H", ("OU_HOME", "2H")),
        ("1X2_1H", ("1X2", "1H")),
        ("BTTS", ("BTTS", "FT")),
    ],
)
def test_split_period(market: str, expected: tuple[str, str]) -> None:
    assert split_period(market) == expected


def test_period_score_table() -> None:
    d = Decision("score", (3, 1), (1, 1))
    assert period_score(d, "OU") == (3, 1)
    assert period_score(d, "OU_1H") == (1, 1)
    assert period_score(d, "1X2_2H") == (2, 0)
    assert period_score(Decision("score", (3, 1), None), "OU_1H") is None
    assert period_score(Decision("score", (3, 1), None), "AH") == (3, 1)


@pytest.mark.parametrize(
    ("market", "selection", "line", "half_score", "expected"),
    [
        ("OU_1H", "over", 0.5, (1, 0), "win"),
        ("OU_1H", "under", 1.5, (1, 1), "loss"),
        ("1X2_2H", "draw", 0.0, (0, 0), "win"),
        ("DC_1H", "home_draw", 0.0, (0, 1), "loss"),
        ("AH_2H", "away", -0.5, (1, 1), "win"),  # home line -0.5: away +0.5 wins on a draw
        ("BTTS_1H", "yes", 0.0, (1, 1), "win"),
        ("OU_AWAY_2H", "over", 0.5, (3, 0), "loss"),
    ],
)
def test_settle_half_markets(
    market: str, selection: str, line: float, half_score: tuple[int, int], expected: str
) -> None:
    assert settle(market, selection, line, *half_score) == expected  # type: ignore[arg-type]


def test_first_half_score_from_sportybet() -> None:
    ok = {"gameScore": ["1:0", "1:2"], "setScore": "2:2"}
    assert first_half_score(ok) == (1, 0)
    assert first_half_score({"gameScore": ["1:0", "1:2"], "setScore": "3:2"}) is None
    assert first_half_score({"gameScore": ["1:0"]}) is None
