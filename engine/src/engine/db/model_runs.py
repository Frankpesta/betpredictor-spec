"""Fit a league from the DB and persist it as a `model_runs` row (docs/03, `make fit`)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from engine.config import LEAGUES, Settings
from engine.db.models import League, ModelRun
from engine.db.queries import (
    current_season_teams,
    load_finished_matches,
    load_half_time_goals,
    team_names,
)
from engine.model.half_time import HalfTimeParams, fit_for_model
from engine.model.league import FitSettings, LeagueMatches, LeagueModel, fit_league


def fit_settings(settings: Settings, xi_per_day: float | None = None) -> FitSettings:
    m = settings.model
    return FitSettings(
        xi_per_day=m.xi_per_day if xi_per_day is None else xi_per_day,
        ridge_lambda=m.ridge_lambda,
        rho_bounds=m.rho_bounds,
        train_seasons_back=m.train_seasons_back,
        min_team_matches=m.min_team_matches,
    )


def params_json(
    model: LeagueModel,
    settings: Settings,
    names: dict[int, str],
    half_time: HalfTimeParams | None = None,
) -> str:
    g = model.goals
    return json.dumps(
        {
            # docs/10 §2: None for INTL or when too few matches have half-time goals
            "half_time": None if half_time is None else half_time.to_json(),
            "goals": g.params.to_json_dict(),
            "xg": None if model.xg is None else model.xg.params.to_json_dict(),
            "xi_per_day": settings.model.xi_per_day,
            "xg_blend_weight": settings.model.xg_blend_weight,
            "ridge_lambda": settings.model.ridge_lambda,
            "low_confidence": sorted(model.low_confidence),
            "matches_per_team": {str(k): v for k, v in g.matches_per_team.items()},
            "team_names": {str(t): names.get(t, "?") for t in g.params.teams},
            "n_xg_matches": model.n_xg_matches,
            "goals_retried": g.retried,
            "xg_retried": None if model.xg is None else model.xg.retried,
        },
        sort_keys=True,
    )


@dataclass(frozen=True)
class StoredFit:
    run: ModelRun
    model: LeagueModel


def fit_and_store(
    session: Session, league: League, settings: Settings, fit_time: datetime
) -> StoredFit:
    matches = load_finished_matches(session, league.id)
    model = fit_league(matches, fit_time.timestamp(), fit_settings(settings))
    names = team_names(session)
    half_time = fit_half_time_params(session, league, matches, model, settings, fit_time)
    run = ModelRun(
        league_id=league.id,
        model_version=settings.model.version,
        fitted_at=fit_time,
        train_from=datetime.fromtimestamp(model.window_start, UTC).date(),
        train_to=fit_time.date(),
        params_json=params_json(model, settings, names, half_time),
        converged=model.goals.converged and (model.xg is None or model.xg.converged),
        neg_log_lik=model.goals.neg_log_lik,
        n_matches=model.goals.n_matches,
    )
    session.add(run)
    session.flush()
    return StoredFit(run, model)


def fit_half_time_params(
    session: Session,
    league: League,
    matches: LeagueMatches,
    model: LeagueModel,
    settings: Settings,
    fit_time: datetime,
) -> HalfTimeParams | None:
    """docs/10 §2: club leagues only (INTL results carry no half-time scores)."""
    if LEAGUES[league.key].international:
        return None
    hth, hta = load_half_time_goals(session, league.id, matches.match_id)
    return fit_for_model(
        model,
        matches,
        hth,
        hta,
        fit_time.timestamp(),
        settings.model.train_seasons_back,
        settings.model.rho_bounds,
        settings.model.xg_blend_weight,
        settings.half_time.min_matches,
    )


def format_fit_summary(
    session: Session, league: League, stored: StoredFit, settings: Settings, season: str
) -> str:
    m, run = stored.model, stored.run
    names = team_names(session)
    current = current_season_teams(session, league.id, season)
    ratings = m.ratings(settings.model.xg_blend_weight)
    ranked = sorted(
        ((r, t) for t, r in ratings.items() if not current or t in current), reverse=True
    )
    g = m.goals.params

    def row(r: float, t: int) -> str:
        n = m.goals.matches_per_team.get(t, 0)
        flag = "  (low confidence)" if t in m.low_confidence else ""
        return f"    {names.get(t, t)!s:<22} {r:+.3f}  n={n}{flag}"

    xg_part = (
        "no xG model"
        if m.xg is None
        else f"xG model on {m.n_xg_matches} matches, h_xg={m.xg.params.home_adv:+.3f}"
    )
    lines = [
        f"{league.key}: run #{run.id}  n={run.n_matches}  converged={run.converged}"
        f"{' (retried)' if m.goals.retried else ''}  window {run.train_from}..{run.train_to}",
        f"  h={g.home_adv:+.3f}  rho={g.rho:+.3f}  gamma={g.gamma:+.3f}  {xg_part}",
        f"  top 5 by att-def ({season} teams):",
        *(row(r, t) for r, t in ranked[:5]),
        "  bottom 5:",
        *(row(r, t) for r, t in ranked[-5:]),
    ]
    missing = sorted(current - set(ratings))
    if missing:
        lines.append(
            "  no training data (cannot be predicted yet): "
            + ", ".join(names.get(t, str(t)) for t in missing)
        )
    return "\n".join(lines)
