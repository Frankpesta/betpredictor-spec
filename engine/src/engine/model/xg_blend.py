"""xG model and goals/xG blending (docs/03 §4-5).

The xG model has the Dixon-Coles structure without τ (ρ fixed at 0), fitted to xG
values as a Poisson quasi-likelihood. Blending mixes the two models' log-rates.
"""

from __future__ import annotations

import math

from engine.model.dixon_coles import DCParams, FitResult, TrainingData, fit


def fit_xg(data: TrainingData, *, ridge: float) -> FitResult:
    """Fit the xG model: same decay and ridge as the goals model, no τ."""
    return fit(data, ridge=ridge, use_tau=False)


def blend_rates(
    goals: DCParams,
    xg: DCParams | None,
    xg_blend_weight: float,
    home_team: int,
    away_team: int,
    neutral: bool = False,
) -> tuple[float, float]:
    """(λ, μ) with log λ = w·log λ_g + (1−w)·log λ_x (docs/03 §5).

    Without an xG model — or when either team is missing from it — the goals
    model is used alone (equivalent to w = 1).
    """
    log_lam_g, log_mu_g = goals.log_rates(home_team, away_team, neutral)
    if xg is None or not (xg.has_team(home_team) and xg.has_team(away_team)):
        return math.exp(log_lam_g), math.exp(log_mu_g)
    log_lam_x, log_mu_x = xg.log_rates(home_team, away_team, neutral)
    w = xg_blend_weight
    return (
        math.exp(w * log_lam_g + (1 - w) * log_lam_x),
        math.exp(w * log_mu_g + (1 - w) * log_mu_x),
    )
