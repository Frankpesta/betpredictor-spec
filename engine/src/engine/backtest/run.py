"""`make backtest`: tuning grid -> holdout -> metrics -> report + `backtest_runs` row."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from engine.backtest.metrics import (
    all_metrics,
    ou_calibration_without_odds,
    simulate_daily_slips,
)
from engine.backtest.report import render
from engine.backtest.tuning import gate, tune
from engine.backtest.walk_forward import (
    MatchMarkets,
    MatchPrice,
    SeasonPrices,
    SeasonTask,
    ValueParams,
    evaluate,
    run_tasks,
)
from engine.db.base import utcnow
from engine.db.model_runs import fit_settings
from engine.db.models import BacktestRun
from engine.db.queries import league_by_key, load_finished_matches, load_match_markets
from engine.db.session import session_scope
from engine.logging import get_logger
from engine.model.league import LeagueMatches

if TYPE_CHECKING:
    from engine.jobs import JobContext

log = get_logger(__name__)


def _tasks(
    data: dict[str, tuple[LeagueMatches, list[MatchMarkets]]],
    seasons: list[str],
    xis: list[float],
    blend_weights: list[float],
    refit_days: int,
    ctx: JobContext,
) -> list[SeasonTask]:
    tasks = []
    for league, (history, markets) in data.items():
        for season in seasons:
            season_matches = tuple(m for m in markets if m.season == season)
            if not season_matches:
                ctx.warn(f"backtest: no finished matches for {league} {season}")
                continue
            for xi in xis:
                tasks.append(
                    SeasonTask(
                        league=league,
                        season=season,
                        history=history,
                        matches=season_matches,
                        fit=fit_settings(ctx.settings, xi_per_day=xi),
                        blend_weights=tuple(blend_weights),
                        refit_days=refit_days,
                        max_goals=ctx.settings.model.max_goals,
                    )
                )
    return tasks


def _collect(results: list[SeasonPrices]) -> dict[float, list[MatchPrice]]:
    by_xi: dict[float, list[MatchPrice]] = defaultdict(list)
    for r in results:
        by_xi[r.xi_per_day].extend(r.prices)
    return by_xi


def _notes(results: list[SeasonPrices], label: str) -> list[str]:
    """Fit counts over all grid points; match counts once per match (first xi only)."""
    first_xi = min((r.xi_per_day for r in results), default=None)
    once = [r for r in results if r.xi_per_day == first_xi]
    blocks = sum(r.n_blocks for r in results)
    retried = sum(r.n_fits_retried for r in results)
    unknown = sum(r.skipped_unknown_team for r in once)
    ah_q = sum(r.ah_non_half for r in once)
    absurd = sorted({a for r in once for a in r.skipped_absurd})
    extra = (
        [f"{label}: {len(absurd)} match-predictions skipped because the model's rates were "
         "absurd (score-matrix tail check): " + "; ".join(absurd[:10])]
        if absurd
        else []
    )  # fmt: skip
    return [
        *extra,
        f"{label}: {blocks} fits (all grid points), {retried} needed the optimiser retry; "
        f"{unknown} matches skipped because a team had no training data "
        f"(promoted with no history in the window); {ah_q} matches opened on a whole/quarter "
        "AH line and were not priced for AH (v1 bets half lines only).",
    ]


def _intl_calibration(
    ctx: JobContext,
    intl_data: dict[str, tuple[LeagueMatches, list[MatchMarkets]]],
    xi: float,
) -> dict[str, Any]:
    """docs/09 §4: walk-forward O/U 2.5 calibration for INTL over all test seasons."""
    out: dict[str, Any] = {}
    bt = ctx.settings.backtest
    first_test_start = min((m.kickoff for _, ms in intl_data.values() for m in ms), default=None)
    for key, (history, ms) in intl_data.items():
        results = run_tasks(
            ctx.warn,
            _tasks({key: (history, ms)}, bt.test_seasons, [xi], [1.0], bt.refit_every_days, ctx),
        )
        by_id = {m.match_id: m for m in ms}
        prices = [p for r in results for p in r.prices]
        went_over = [
            int(by_id[p.match_id].home_goals + by_id[p.match_id].away_goals > 2.5) for p in prices
        ]
        prior = history.kickoff < (first_test_start or 0.0)
        totals = history.home_goals[prior] + history.away_goals[prior]
        base_rate = float((totals > 2.5).mean()) if totals.size else 0.5
        out[key] = {
            **ou_calibration_without_odds([p.p_over for p in prices], went_over, base_rate),
            "seasons": list(bt.test_seasons),
            "skipped_unknown_team": sum(r.skipped_unknown_team for r in results),
            "skipped_absurd_rates": sorted({a for r in results for a in r.skipped_absurd}),
        }
    return out


def run_backtest(ctx: JobContext) -> str:
    s = ctx.settings
    bt = s.backtest
    t0 = time.monotonic()
    started = utcnow()
    tuning_seasons, holdout = bt.test_seasons[:-1], bt.test_seasons[-1]

    data: dict[str, tuple[LeagueMatches, list[MatchMarkets]]] = {}
    intl_data: dict[str, tuple[LeagueMatches, list[MatchMarkets]]] = {}
    with session_scope(ctx.db_path) as sess:
        for lg in s.enabled_leagues():
            league = league_by_key(sess, lg.key)
            target = intl_data if lg.international else data
            target[lg.key] = (
                load_finished_matches(sess, league.id),
                load_match_markets(
                    sess, league, bt.test_seasons, bt.odds_source, bt.closing_source,
                    bt.closing_fallback,
                ),
            )  # fmt: skip
    markets = {m.match_id: m for _, ms in data.values() for m in ms}

    base = ValueParams(
        shrink_weight=s.value.market_shrink_weight,
        min_edge=s.value.min_edge_leg,
        min_odds=s.value.min_odds,
        max_odds=s.value.max_odds,
        max_model_market_gap=s.value.max_model_market_gap,
    )

    # ---- tuning grid (refit every tuning_refit_every_days) ----
    tuning_results = run_tasks(
        ctx.warn,
        _tasks(
            data, tuning_seasons, bt.grid_xi_per_day, bt.grid_xg_blend_weight,
            bt.tuning_refit_every_days, ctx,
        )
    )  # fmt: skip
    tuning = tune(
        _collect(tuning_results),
        markets,
        bt.grid_xg_blend_weight,
        bt.grid_market_shrink_weight,
        bt.grid_min_edge_leg,
        base,
        s.gates.min_bets_for_roi,
    )
    chosen = ValueParams(
        shrink_weight=tuning.market_shrink_weight,
        min_edge=tuning.min_edge_leg,
        min_odds=base.min_odds,
        max_odds=base.max_odds,
        max_model_market_gap=base.max_model_market_gap,
    )
    tuning_prices = _collect(tuning_results)[tuning.xi_per_day]
    tuning_records = evaluate(tuning_prices, markets, tuning.xg_blend_weight, chosen)
    log.info("backtest tuning done", extra={"fields": {"seconds": round(time.monotonic() - t0)}})

    # ---- holdout: run once with the chosen parameters, refit every refit_every_days ----
    holdout_results = run_tasks(
        ctx.warn,
        _tasks(
            data, [holdout], [tuning.xi_per_day], [tuning.xg_blend_weight],
            bt.refit_every_days, ctx,
        )
    )  # fmt: skip
    holdout_records = evaluate(
        _collect(holdout_results)[tuning.xi_per_day], markets, tuning.xg_blend_weight, chosen
    )
    holdout_metrics = all_metrics(holdout_records, bt.closing_source)
    tuning_metrics = all_metrics(tuning_records, bt.closing_source)
    slips = simulate_daily_slips(holdout_records, s.slips.daily_2odds, s.tz)
    g = gate(holdout_metrics["overall"]["ALL"], s.gates)

    intl = _intl_calibration(ctx, intl_data, tuning.xi_per_day)

    notes = _notes(tuning_results, "tuning") + _notes(holdout_results, "holdout")
    notes.append(
        f"Bet price = {bt.odds_source} opening odds; closing benchmark = {bt.closing_source} "
        f"closing odds, falling back to {bt.closing_fallback} where {bt.closing_source} is "
        "missing (user decision 2026-09-27). AH CLV/closing baselines only where the closing "
        "line equals the opening line."
    )
    notes.append(
        f"CLV caveat: {bt.closing_fallback} closing odds include bookmaker margins above "
        f"{bt.closing_source}'s, so CLV measured against the fallback is biased upward. "
        f"Trust the {bt.closing_source}-only CLV column more."
    )
    notes.append(
        "Probability metrics use one row per priced half-line market (over / home side); "
        "all priced rows count, qualifying or not. Betting metrics: flat 1 unit per "
        "qualifying selection, in kickoff order."
    )

    report_text = render(
        started=started.strftime("%Y-%m-%d %H:%M UTC"),
        tuning_seasons=tuning_seasons,
        holdout_season=holdout,
        tuning=tuning,
        holdout_metrics=holdout_metrics,
        tuning_metrics=tuning_metrics,
        slips=slips,
        gate_result=g,
        primary_closing=bt.closing_source,
        notes=notes,
        intl=intl,
    )
    out_dir = s.reports_path
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"backtest_{started:%Y%m%d_%H%M%S}.md"
    path.write_text(report_text, encoding="utf-8")

    metrics_json: dict[str, Any] = {
        "tuning": {
            "stage1": [r.__dict__ for r in tuning.stage1],
            "stage2": [r.__dict__ for r in tuning.stage2],
            "stage3": [r.__dict__ for r in tuning.stage3],
            "chosen": {
                "xi_per_day": tuning.xi_per_day,
                "xg_blend_weight": tuning.xg_blend_weight,
                "market_shrink_weight": tuning.market_shrink_weight,
                "min_edge_leg": tuning.min_edge_leg,
                "min_edge_fallback": tuning.min_edge_fallback,
            },
        },
        "holdout": holdout_metrics,
        "tuning_seasons_chosen_params": tuning_metrics,
        "holdout_daily_2odds": slips,
        "gate": g.__dict__,
        "international_unvalidated": intl,
    }
    with session_scope(ctx.db_path) as sess:
        row = BacktestRun(
            started_at=started,
            finished_at=utcnow(),
            config_json=json.dumps(s.model_dump(mode="json"), sort_keys=True),
            metrics_json=json.dumps(metrics_json, default=str, sort_keys=True),
            gate_passed=g.passed,
            report_path=str(path),
        )
        sess.add(row)
        sess.flush()
        ctx.note("backtest_run_id", row.id)
    ctx.note("gate_passed", g.passed)
    ctx.note("report_path", str(path))
    ctx.note("seconds", round(time.monotonic() - t0))
    return report_text
