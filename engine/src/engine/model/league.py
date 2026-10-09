"""One league's fitted model: goals DC + optional xG model + blending (docs/03 §3-7).

Pure. `LeagueMatches` holds a league's history as arrays; `fit_league` selects the
training window strictly before the fit time and fits both models.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from engine.model.dixon_coles import DCParams, FitResult, build_training_data, fit
from engine.model.score_matrix import score_matrix
from engine.model.xg_blend import blend_rates, fit_xg

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

DAYS_PER_SEASON = 365  # docs/03 §3 window = train_seasons_back × 365 days (user decision)
SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True)
class LeagueMatches:
    """Finished matches of one league. `kickoff` is UTC epoch seconds; xG may be NaN."""

    match_id: IntArray
    kickoff: FloatArray
    home: IntArray
    away: IntArray
    home_goals: FloatArray
    away_goals: FloatArray
    home_xg: FloatArray
    away_xg: FloatArray
    neutral: FloatArray  # 1.0 at a neutral venue (internationals), else 0.0

    def __len__(self) -> int:
        return int(self.match_id.shape[0])

    def subset(self, mask: npt.NDArray[np.bool_]) -> LeagueMatches:
        return LeagueMatches(
            self.match_id[mask],
            self.kickoff[mask],
            self.home[mask],
            self.away[mask],
            self.home_goals[mask],
            self.away_goals[mask],
            self.home_xg[mask],
            self.away_xg[mask],
            self.neutral[mask],
        )


@dataclass(frozen=True)
class FitSettings:
    xi_per_day: float
    ridge_lambda: float
    rho_bounds: tuple[float, float]
    train_seasons_back: int
    min_team_matches: int


@dataclass(frozen=True)
class LeagueModel:
    fit_time: float  # epoch seconds; every training match kicked off strictly before it
    window_start: float
    goals: FitResult
    xg: FitResult | None
    low_confidence: frozenset[int]
    n_xg_matches: int

    def has_team(self, team: int) -> bool:
        return self.goals.params.has_team(team)

    def rates(
        self, home: int, away: int, xg_blend_weight: float, neutral: bool = False
    ) -> tuple[float, float]:
        return blend_rates(
            self.goals.params,
            None if self.xg is None else self.xg.params,
            xg_blend_weight,
            home,
            away,
            neutral,
        )

    def matrix(
        self,
        home: int,
        away: int,
        xg_blend_weight: float,
        max_goals: int,
        neutral: bool = False,
    ) -> FloatArray:
        """Score matrix; ρ always from the goals model (docs/03 §5)."""
        lam, mu = self.rates(home, away, xg_blend_weight, neutral)
        return score_matrix(lam, mu, self.goals.params.rho, max_goals)

    def ratings(self, xg_blend_weight: float) -> dict[int, float]:
        """att − def in blended log-rate space (higher = stronger)."""
        g = self.goals.params
        out: dict[int, float] = {}
        for i, t in enumerate(g.teams):
            r = float(g.att[i] - g.defence[i])
            if self.xg is not None and self.xg.params.has_team(t):
                x = self.xg.params
                j = x.teams.index(t)
                r = xg_blend_weight * r + (1 - xg_blend_weight) * float(x.att[j] - x.defence[j])
            out[t] = r
        return out


def training_window(fit_time: float, train_seasons_back: int) -> tuple[float, float]:
    return fit_time - train_seasons_back * DAYS_PER_SEASON * SECONDS_PER_DAY, fit_time


def fit_league(matches: LeagueMatches, fit_time: float, cfg: FitSettings) -> LeagueModel:
    """Fit goals + xG models on finished matches in [fit_time − window, fit_time)."""
    start, end = training_window(fit_time, cfg.train_seasons_back)
    train = matches.subset((matches.kickoff >= start) & (matches.kickoff < end))
    # docs/03 §10.1: no leakage — enforced here for every fit, not only in tests.
    assert len(train) == 0 or float(train.kickoff.max()) < fit_time, "training data leaks"

    days_before = (fit_time - train.kickoff) / SECONDS_PER_DAY
    goals_data = build_training_data(
        train.home,
        train.away,
        train.home_goals,
        train.away_goals,
        days_before,
        cfg.xi_per_day,
        train.neutral,
    )
    goals = fit(goals_data, ridge=cfg.ridge_lambda, rho_bounds=cfg.rho_bounds, use_tau=True)

    has_xg = ~np.isnan(train.home_xg) & ~np.isnan(train.away_xg)
    xg: FitResult | None = None
    if has_xg.any():
        xt = train.subset(has_xg)
        xg_data = build_training_data(
            xt.home,
            xt.away,
            xt.home_xg,
            xt.away_xg,
            days_before[has_xg],
            cfg.xi_per_day,
            xt.neutral,
        )
        xg = fit_xg(xg_data, ridge=cfg.ridge_lambda)

    return LeagueModel(
        fit_time=fit_time,
        window_start=start,
        goals=goals,
        xg=xg,
        low_confidence=frozenset(goals.low_confidence(cfg.min_team_matches)),
        n_xg_matches=int(has_xg.sum()),
    )


def params_from_json(d: dict[str, object]) -> DCParams:
    teams = d["teams"]
    att, dfn = d["att"], d["def"]
    assert isinstance(teams, list) and isinstance(att, list) and isinstance(dfn, list)
    return DCParams(
        teams=tuple(int(t) for t in teams),
        att=np.array(att, dtype=np.float64),
        defence=np.array(dfn, dtype=np.float64),
        gamma=float(d["gamma"]),  # type: ignore[arg-type]
        home_adv=float(d["home_adv"]),  # type: ignore[arg-type]
        rho=float(d["rho"]),  # type: ignore[arg-type]
    )


@dataclass(frozen=True)
class StoredModel:
    """A model rebuilt from `model_runs.params_json` — predictions use exactly that run."""

    goals: DCParams
    xg: DCParams | None
    low_confidence: frozenset[int]
    half_time: dict[str, float | int] | None = None  # docs/10 §2 (HalfTimeParams JSON)

    @classmethod
    def from_json(cls, params: dict[str, object]) -> StoredModel:
        goals = params["goals"]
        xg = params.get("xg")
        low = params.get("low_confidence") or []
        ht = params.get("half_time")
        assert isinstance(goals, dict) and isinstance(low, list)
        return cls(
            goals=params_from_json(goals),
            xg=params_from_json(xg) if isinstance(xg, dict) else None,
            low_confidence=frozenset(int(t) for t in low),
            half_time=ht if isinstance(ht, dict) else None,
        )

    def has_team(self, team: int) -> bool:
        return self.goals.has_team(team)

    def rates(
        self, home: int, away: int, xg_blend_weight: float, neutral: bool = False
    ) -> tuple[float, float]:
        return blend_rates(self.goals, self.xg, xg_blend_weight, home, away, neutral)

    def matrix(
        self, home: int, away: int, xg_blend_weight: float, max_goals: int, neutral: bool = False
    ) -> FloatArray:
        lam, mu = self.rates(home, away, xg_blend_weight, neutral)
        return score_matrix(lam, mu, self.goals.rho, max_goals)
