"""`make backtest-ht`: walk-forward calibration backtest of the half-time model (docs/10 §4).

No historical half-time odds exist, so this measures calibration only — never ROI.
Per league-season (worker process): step through blocks of `refit_every_days`; at each
block start fit the full-time model on matches strictly before it, fit the half-time
shares and ρ on that model's training window, then price both halves of every match in
the block. A no-team-strength baseline (league-average rates × the same shares, own ρ)
is priced alongside. The main process scores each (half, market) and applies the gate.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
from sqlalchemy import select

from engine.backtest.walk_forward import season_blocks
from engine.db.base import utcnow
from engine.db.model_runs import fit_settings
from engine.db.models import Match
from engine.db.queries import league_by_key, load_finished_matches
from engine.db.session import session_scope
from engine.logging import get_logger
from engine.model.calibration import brier, ece, log_loss
from engine.model.half_time import fit_half_time, half_matrix, training_rows
from engine.model.league import FitSettings, LeagueMatches, fit_league
from engine.model.markets import Market, Selection, price_selection
from engine.model.score_matrix import AbsurdRatesError, FloatArray

if TYPE_CHECKING:
    from engine.jobs import JobContext

log = get_logger(__name__)

# docs/10 §4: (label, market, selection, line); "1X2" rows are scored as one 3-way market.
SCORED: tuple[tuple[str, Market, Selection, float], ...] = (
    ("O/U 0.5", "OU", "over", 0.5),
    ("O/U 1.5", "OU", "over", 1.5),
    ("O/U 2.5", "OU", "over", 2.5),
    ("1X2", "1X2", "home", 0.0),
    ("1X2", "1X2", "draw", 0.0),
    ("1X2", "1X2", "away", 0.0),
    ("GG/NG", "BTTS", "yes", 0.0),
    ("Home over 0.5", "OU_HOME", "over", 0.5),
    ("Away over 0.5", "OU_AWAY", "over", 0.5),
)
MARKET_LABELS = tuple(dict.fromkeys(label for label, *_ in SCORED))


@dataclass(frozen=True)
class HtTask:
    league: str
    season: str
    history: LeagueMatches
    ht_home: FloatArray  # aligned with history; NaN = no half-time goals
    ht_away: FloatArray
    season_mask: np.ndarray  # history rows of this season
    fit: FitSettings
    blend: float
    refit_days: int
    max_goals: int


@dataclass
class HtRows:
    """One row per (match, half, scored selection)."""

    league: str
    season: str
    half: list[int] = field(default_factory=list)
    label: list[str] = field(default_factory=list)
    p_model: list[float] = field(default_factory=list)
    p_base: list[float] = field(default_factory=list)
    outcome: list[float] = field(default_factory=list)
    params: list[dict[str, Any]] = field(default_factory=list)  # per block (report)
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _half_score(h: int, a: int, hth: int, hta: int, half: int) -> tuple[int, int]:
    return (hth, hta) if half == 1 else (h - hth, a - hta)


def price_season(task: HtTask) -> HtRows:
    out = HtRows(task.league, task.season)
    hist = task.history
    has_ht = ~np.isnan(task.ht_home) & ~np.isnan(task.ht_away)
    idx_season = np.flatnonzero(task.season_mask)
    for start, end in season_blocks(hist.kickoff[idx_season], task.refit_days):
        block = idx_season[(hist.kickoff[idx_season] >= start) & (hist.kickoff[idx_season] < end)]
        if block.size == 0:
            continue
        model = fit_league(hist, start, task.fit)
        train = training_rows(
            model, hist, task.ht_home, task.ht_away, start, task.fit.train_seasons_back
        )
        if train.size < 50:
            out.skipped["block_too_little_ht_training"] += int(block.size)
            continue
        rates = np.array(
            [model.rates(int(hist.home[i]), int(hist.away[i]), task.blend) for i in train]
        )
        args = (
            hist.home_goals[train],
            hist.away_goals[train],
            task.ht_home[train],
            task.ht_away[train],
            task.fit.rho_bounds,
        )
        params = fit_half_time(rates[:, 0], rates[:, 1], *args)
        base_l, base_m = float(hist.home_goals[train].mean()), float(hist.away_goals[train].mean())
        base = fit_half_time(np.full(train.size, base_l), np.full(train.size, base_m), *args)
        out.params.append({"block_start": start, **params.to_json()})
        for i in block:
            if not has_ht[i]:
                out.skipped["no_ht_goals"] += 1
                continue
            home, away = int(hist.home[i]), int(hist.away[i])
            if not (model.has_team(home) and model.has_team(away)):
                out.skipped["team_not_in_model"] += 1
                continue
            lam, mu = model.rates(home, away, task.blend)
            score = (
                int(hist.home_goals[i]),
                int(hist.away_goals[i]),
                int(task.ht_home[i]),
                int(task.ht_away[i]),
            )
            for half in (1, 2):
                try:
                    mat = half_matrix(lam, mu, params, half, task.max_goals)
                    bmat = half_matrix(base_l, base_m, base, half, task.max_goals)
                except AbsurdRatesError:
                    out.skipped["absurd_rates"] += 1
                    continue
                hs, as_ = _half_score(*score, half)
                for label, market, sel, line in SCORED:
                    won = price_selection(_one(hs, as_), market, sel, line).p_win
                    out.half.append(half)
                    out.label.append(label)
                    out.p_model.append(price_selection(mat, market, sel, line).p_win)
                    out.p_base.append(price_selection(bmat, market, sel, line).p_win)
                    out.outcome.append(won)
    return out


def _one(h: int, a: int) -> FloatArray:
    """A score matrix with all mass on the observed score: settles through the same code."""
    m = np.zeros((h + 1, a + 1))
    m[h, a] = 1.0
    return m


def run_tasks(tasks: Sequence[HtTask]) -> list[HtRows]:
    workers = min(len(tasks), os.cpu_count() or 1)
    if workers <= 1:
        return [price_season(t) for t in tasks]
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(price_season, tasks))
    except (BrokenProcessPool, OSError) as exc:
        log.warning(
            "backtest-ht: process pool failed, running sequentially",
            extra={"fields": {"error": str(exc)}},
        )
        return [price_season(t) for t in tasks]


@dataclass(frozen=True)
class MarketScore:
    half: int
    label: str
    n: int  # matches
    log_loss: float
    log_loss_base: float
    brier: float
    ece: float
    passed: bool


def score_market(
    half: int, label: str, p: FloatArray, pb: FloatArray, y: FloatArray, max_ece: float
) -> MarketScore:
    """docs/10 §4. 1X2: rows come in (home, draw, away) triples; log loss is the 3-way
    −log p(actual) per match, ECE pools the three one-vs-rest rows."""
    if label == "1X2":
        n = p.size // 3
        won = y.astype(bool)
        ll, llb = (
            float(-np.log(np.clip(p[won], 1e-15, 1)).mean()),
            float(-np.log(np.clip(pb[won], 1e-15, 1)).mean()),
        )
        br = float(((p - y) ** 2).reshape(n, 3).sum(axis=1).mean())
    else:
        n = p.size
        ll, llb, br = log_loss(p, y), log_loss(pb, y), brier(p, y)
    e = ece(p, y)
    return MarketScore(half, label, n, ll, llb, br, e, passed=e <= max_ece and ll < llb)


def score_all(rows: Sequence[HtRows], max_ece: float) -> list[MarketScore]:
    half = np.concatenate([np.array(r.half) for r in rows])
    label = np.concatenate([np.array(r.label) for r in rows])
    p = np.concatenate([np.array(r.p_model) for r in rows])
    pb = np.concatenate([np.array(r.p_base) for r in rows])
    y = np.concatenate([np.array(r.outcome) for r in rows])
    out = []
    for h in (1, 2):
        for lab in MARKET_LABELS:
            m = (half == h) & (label == lab)
            if m.any():
                out.append(score_market(h, lab, p[m], pb[m], y[m], max_ece))
    return out


def _load(ctx: JobContext) -> list[HtTask]:
    s = ctx.settings
    tasks: list[HtTask] = []
    with session_scope(ctx.db_path) as sess:
        for lg in s.enabled_leagues():
            if lg.international:
                continue  # docs/10: clubs only (no half-time scores for INTL)
            league = league_by_key(sess, lg.key)
            hist = load_finished_matches(sess, league.id)
            extra = {
                r[0]: r[1:]
                for r in sess.execute(
                    select(Match.id, Match.season, Match.ht_home_goals, Match.ht_away_goals).where(
                        Match.league_id == league.id, Match.status == "finished"
                    )
                )
            }
            seasons = np.array([extra[int(i)][0] for i in hist.match_id])
            hth = np.array(
                [np.nan if extra[int(i)][1] is None else extra[int(i)][1] for i in hist.match_id],
                dtype=float,
            )
            hta = np.array(
                [np.nan if extra[int(i)][2] is None else extra[int(i)][2] for i in hist.match_id],
                dtype=float,
            )
            if np.isnan(hth).all():
                ctx.warn(f"backtest-ht: {lg.key} has no half-time goals — run `make ingest`")
                continue
            for season in s.backtest.test_seasons:
                mask = seasons == season
                if not mask.any():
                    ctx.warn(f"backtest-ht: no finished matches for {lg.key} {season}")
                    continue
                tasks.append(
                    HtTask(
                        league=lg.key,
                        season=season,
                        history=hist,
                        ht_home=hth,
                        ht_away=hta,
                        season_mask=mask,
                        fit=fit_settings(s),
                        blend=s.model.xg_blend_weight,
                        refit_days=s.backtest.refit_every_days,
                        max_goals=s.model.max_goals,
                    )
                )
    return tasks


def render(scores: Sequence[MarketScore], rows: Sequence[HtRows], max_ece: float) -> str:
    lines = [
        f"# Half-time model backtest (docs/10 §4) — {utcnow():%Y-%m-%d %H:%M} UTC",
        "",
        "Calibration only: there are no historical half-time odds, so nothing here says the "
        "model can beat SportyBet's prices. Gate per market and half: ECE <= "
        f"{max_ece} and log loss below the no-team-strength baseline.",
        "",
        "| half | market | matches | log loss | baseline | Brier | ECE | gate |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for sc in scores:
        lines.append(
            f"| {sc.half}H | {sc.label} | {sc.n} | {sc.log_loss:.4f} | {sc.log_loss_base:.4f} | "
            f"{sc.brier:.4f} | {sc.ece:.4f} | {'PASS' if sc.passed else 'fail'} |"
        )
    lines += ["", "## Fitted half-time parameters (mean over blocks)", ""]
    by_league: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_league[r.league] += r.params
    for lg, ps in sorted(by_league.items()):
        if ps:
            mean = {
                k: float(np.mean([p[k] for p in ps]))
                for k in ("share_home", "share_away", "rho_1h", "rho_2h")
            }
            lines.append(
                f"- {lg}: first-half share home {mean['share_home']:.3f}, away "
                f"{mean['share_away']:.3f}; rho 1H {mean['rho_1h']:+.3f}, 2H {mean['rho_2h']:+.3f} "
                f"({len(ps)} blocks)"
            )
    skipped: dict[str, int] = defaultdict(int)
    for r in rows:
        for k, v in r.skipped.items():
            skipped[k] += v
    lines += [
        "",
        "Skipped: " + (", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) or "none"),
    ]
    return "\n".join(lines) + "\n"


def run_backtest_ht(ctx: JobContext) -> str:
    s = ctx.settings
    tasks = _load(ctx)
    if not tasks:
        raise RuntimeError("backtest-ht: nothing to backtest (no club half-time data)")
    rows = run_tasks(tasks)
    max_ece = s.gates.max_calibration_ece
    scores = score_all(rows, max_ece)
    report = render(scores, rows, max_ece)
    out_dir = s.reports_path
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{utcnow():%Y-%m-%d_%H%M}"
    (out_dir / f"half_time_backtest_{stamp}.md").write_text(report, encoding="utf-8")
    payload = [sc.__dict__ for sc in scores]
    (out_dir / f"half_time_backtest_{stamp}.json").write_text(
        json.dumps(payload, indent=1), encoding="utf-8"
    )
    for r in rows:
        for k, v in r.skipped.items():
            ctx.skip(f"ht_{k}", v)
    ctx.note("passed", [f"{sc.half}H {sc.label}" for sc in scores if sc.passed])
    ctx.note("failed", [f"{sc.half}H {sc.label}" for sc in scores if not sc.passed])
    ctx.note("report", str(out_dir / f"half_time_backtest_{stamp}.md"))
    return report
