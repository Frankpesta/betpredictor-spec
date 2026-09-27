"""Backtest: blocks, leakage, value evaluation, metrics arithmetic, gate."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from engine.backtest import walk_forward as wf
from engine.backtest.metrics import _bet_metrics, all_metrics, simulate_daily_slips
from engine.backtest.tuning import gate, tune
from engine.config import get_settings
from engine.model.league import SECONDS_PER_DAY, FitSettings, LeagueMatches
from engine.model.score_matrix import score_matrix

DAY = SECONDS_PER_DAY
T0 = datetime(2023, 8, 12, 14, 0, tzinfo=UTC).timestamp()


def test_season_blocks_cover_every_kickoff() -> None:
    ks = [T0, T0 + 3 * DAY, T0 + 14 * DAY, T0 + 30.5 * DAY]
    blocks = wf.season_blocks(ks, 7)
    assert blocks[0][0] == np.floor(T0 / DAY) * DAY
    assert all(b - a == 7 * DAY for a, b in blocks)
    assert all(any(a <= k < b for a, b in blocks) for k in ks)
    assert wf.season_blocks([], 7) == []


# ---- synthetic league: 3 seasons of 10 teams --------------------------------


def _synthetic(seed: int = 3) -> tuple[LeagueMatches, list[wf.MatchMarkets]]:
    rng = np.random.default_rng(seed)
    n = 10
    att = rng.normal(0, 0.2, n)
    att -= att.mean()
    dfn = rng.normal(0, 0.2, n)
    dfn -= dfn.mean()
    rows = []
    mid = 0
    for season_idx, season in enumerate(("2021-22", "2022-23", "2023-24")):
        start = T0 - (2 - season_idx) * 365 * DAY
        fixtures = [(i, j) for i in range(n) for j in range(n) if i != j]
        for k, (i, j) in enumerate(fixtures):
            lam = np.exp(0.15 + 0.25 + att[i] + dfn[j])
            mu = np.exp(0.15 + att[j] + dfn[i])
            p = score_matrix(lam, mu, -0.05, 15).ravel()
            cell = rng.choice(p.size, p=p)
            hg, ag = divmod(int(cell), 16)
            mid += 1
            rows.append((mid, season, start + (k // 5) * 3.5 * DAY, i + 1, j + 1, hg, ag))
    hist = LeagueMatches(
        match_id=np.array([r[0] for r in rows], dtype=np.int64),
        kickoff=np.array([r[2] for r in rows]),
        home=np.array([r[3] for r in rows], dtype=np.int64),
        away=np.array([r[4] for r in rows], dtype=np.int64),
        home_goals=np.array([r[5] for r in rows], dtype=float),
        away_goals=np.array([r[6] for r in rows], dtype=float),
        home_xg=np.array([r[5] + 0.1 for r in rows], dtype=float),
        away_xg=np.array([r[6] + 0.1 for r in rows], dtype=float),
        neutral=np.zeros(len(rows)),
    )
    markets = [
        wf.MatchMarkets(
            match_id=r[0], league="TST", season=r[1], kickoff=r[2], home=r[3], away=r[4],
            home_goals=r[5], away_goals=r[6], ou_open=(1.95, 1.90), ah_open=(-0.5, 1.95, 1.90),
            closing_ou=wf.ClosingOdds("PS", (1.90, 1.95), None),
            closing_ah=wf.ClosingOdds("AVG", None, (-0.5, 1.93, 1.92)),
        )
        for r in rows
    ]  # fmt: skip
    return hist, markets


FIT = FitSettings(
    xi_per_day=0.0019, ridge_lambda=0.5, rho_bounds=(-0.2, 0.2), train_seasons_back=3,
    min_team_matches=8,
)  # fmt: skip


def _task(hist: LeagueMatches, markets: list[wf.MatchMarkets], season: str) -> wf.SeasonTask:
    return wf.SeasonTask(
        league="TST",
        season=season,
        history=hist,
        matches=tuple(m for m in markets if m.season == season),
        fit=FIT,
        blend_weights=(0.5, 1.0),
        refit_days=14,
        max_goals=15,
    )


def test_price_season_never_trains_on_the_future(monkeypatch: pytest.MonkeyPatch) -> None:
    hist, markets = _synthetic()
    seen: list[tuple[float, float]] = []
    real_fit = wf.fit_league

    def spy(matches: LeagueMatches, fit_time: float, cfg: FitSettings):  # type: ignore[no-untyped-def]
        model = real_fit(matches, fit_time, cfg)
        seen.append((fit_time, model.window_start))
        return model

    monkeypatch.setattr(wf, "fit_league", spy)
    out = wf.price_season(_task(hist, markets, "2023-24"))
    assert out.n_blocks == len(seen) == 5  # 63 days of fixtures, 14-day blocks
    fit_time_of_match = {}
    for fit_time, _ in seen:
        for m in markets:
            if m.season == "2023-24" and fit_time <= m.kickoff < fit_time + 14 * DAY:
                fit_time_of_match[m.match_id] = fit_time
    # every priced match was predicted by a fit made at or before its block start
    for p in out.prices:
        assert fit_time_of_match[p.match_id] <= markets[p.match_id - 1].kickoff
    assert len(out.prices) == 2 * 90  # 90 matches x 2 blend weights


def test_fit_league_rejects_leakage() -> None:
    hist, _ = _synthetic()
    from engine.model.league import fit_league

    t = float(hist.kickoff[200])
    model = fit_league(hist, t, FIT)
    train_n = int(((hist.kickoff < t) & (hist.kickoff >= t - 3 * 365 * DAY)).sum())
    assert model.goals.n_matches == train_n  # the match at exactly t is excluded


def test_evaluate_and_tune_end_to_end() -> None:
    hist, markets = _synthetic()
    res = wf.price_season(_task(hist, markets, "2023-24"))
    mk = {m.match_id: m for m in markets}
    vp = wf.ValueParams(0.7, 0.03, 1.2, 2.6, 0.15)
    recs = wf.evaluate(res.prices, mk, 0.5, vp)
    assert len(recs) == 90 * 4  # OU over/under + AH home/away
    assert sum(r.canonical for r in recs) == 180
    for r in recs:
        assert r.won in (0, 1)
        assert r.return_multiplier in (0.0, r.odds)
        assert (r.closing_source == "PS") if r.market == "OU" else (r.closing_source == "AVG")
    t = tune({0.0019: res.prices}, mk, [0.5, 1.0], [0.5, 1.0], [0.02, 0.05], vp, 10_000)
    assert t.min_edge_fallback and t.min_edge_leg == 0.03  # no setting has 10k bets
    assert (t.xi_per_day, t.xg_blend_weight) in {(0.0019, 0.5), (0.0019, 1.0)}
    m = all_metrics(recs, "PS")
    assert set(m) == {"overall", "TST"}
    assert m["overall"]["ALL"]["n_priced"] == 180


def _rec(
    ret: float, odds: float, kickoff: float, close: float | None, src: str = "PS"
) -> wf.SelectionRecord:
    return wf.SelectionRecord(
        league="TST", season="2023-24", match_id=int(kickoff), kickoff=kickoff, market="OU",
        line=2.5, selection="over", canonical=True, odds=odds, p_model=0.6, p_market_devig=0.55,
        p_final=0.65, expected_multiplier=0.65 * odds, edge=0.65 * odds - 1, qualifies=True,
        reasons=(), won=int(ret > 0), return_multiplier=ret, closing_odds=close,
        closing_source=src if close else None, p_close_devig=0.5 if close else None,
    )  # fmt: skip


def test_bet_metrics_arithmetic() -> None:
    # profits: +1, -1, -1, -1, +1  -> cum 1,0,-1,-2,-1 ; peak 1 -> max DD 3 ; streak 3
    recs = [
        _rec(2.0, 2.0, 1, 1.90),
        _rec(0.0, 2.0, 2, 2.10, "AVG"),
        _rec(0.0, 2.0, 3, None),
        _rec(0.0, 2.0, 4, 2.00),
        _rec(2.0, 2.0, 5, 1.80),
    ]
    m = _bet_metrics(recs, "PS")
    assert m["bets"] == 5 and m["profit_units"] == pytest.approx(-1.0)
    assert m["roi"] == pytest.approx(-0.2) and m["hit_rate"] == pytest.approx(0.4)
    assert m["max_drawdown_units"] == pytest.approx(3.0)
    assert m["longest_losing_streak"] == 3
    clv = [2 / 1.9 - 1, 2 / 2.1 - 1, 0.0, 2 / 1.8 - 1]
    assert m["mean_clv"] == pytest.approx(np.mean(clv)) and m["clv_n"] == 4
    assert m["mean_clv_PS_only"] == pytest.approx(np.mean([2 / 1.9 - 1, 0.0, 2 / 1.8 - 1]))
    assert m["clv_n_by_source"] == {"PS": 3, "AVG": 1} and m["clv_missing"] == 1


def test_daily_slip_simulation_settles_all_or_nothing() -> None:
    cfg = get_settings().slips.daily_2odds
    day = datetime(2025, 3, 1, 15, tzinfo=UTC).timestamp()
    a = _rec(1.40, 1.40, day, None)
    b = _rec(0.0, 1.35, day + 60, None)
    recs = [
        replace(a, match_id=1, p_final=0.76, expected_multiplier=1.064),
        replace(b, match_id=2, p_final=0.78, expected_multiplier=1.053, won=0),
    ]
    out = simulate_daily_slips(recs, cfg, ZoneInfo("Africa/Lagos"))
    assert out["slips"] == 1 and out["hit_rate"] == 0.0 and out["roi"] == -1.0


def test_gate() -> None:
    g = get_settings().gates
    ok = gate({"ece": 0.02, "bets": 500, "roi": 0.01, "mean_clv": 0.005}, g)
    assert ok.passed and ok.failures == []
    bad = gate({"ece": 0.05, "bets": 100, "roi": -0.02}, g)
    assert not bad.passed and len(bad.failures) == 3


def test_broken_process_pool_falls_back_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures.process import BrokenProcessPool

    class Broken:
        def __init__(self, *a: object, **k: object) -> None: ...
        def __enter__(self) -> Broken:
            return self

        def __exit__(self, *a: object) -> None: ...
        def map(self, *a: object) -> list[object]:
            raise BrokenProcessPool("WinError 6")

    hist, markets = _synthetic()
    monkeypatch.setattr(wf, "ProcessPoolExecutor", Broken)
    warnings: list[str] = []
    tasks = [_task(hist, markets, "2023-24"), replace(_task(hist, markets, "2023-24"), league="T2")]
    out = wf.run_tasks(warnings.append, tasks, workers=2)
    assert [r.league for r in out] == ["TST", "T2"]
    assert len(warnings) == 1 and "process pool broke" in warnings[0]
