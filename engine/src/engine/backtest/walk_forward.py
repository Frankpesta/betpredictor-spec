"""Walk-forward backtest (docs/03 §10). Pure apart from the process pool.

Flow:
  1. `price_season` (worker): step through one league-season in blocks of
     `refit_days`; at each block start fit goals + xG models on matches strictly
     before it, then price every match kicking off inside the block for O/U 2.5 and
     AH at the opening `AHh` line, for each xG blend weight. Leakage is asserted
     for every block.
  2. `evaluate` (main process): run the live value pipeline (`value.edge.evaluate_pair`)
     on those prices with a given shrink weight / min edge, and settle each
     selection with `model.markets.settle`.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field

import numpy as np

from engine.model.league import SECONDS_PER_DAY, FitSettings, LeagueMatches, fit_league
from engine.model.markets import (
    OutcomeProbs,
    classify_line,
    price_selection,
    result_multiplier,
    settle,
)
from engine.model.score_matrix import AbsurdRatesError
from engine.value.edge import devig_pair, evaluate_pair

OU_LINE = 2.5  # the only O/U line football-data carries (docs/discovered/football-data.md)


@dataclass(frozen=True)
class ClosingOdds:
    source: str  # bookmaker actually used (closing_source or its fallback)
    ou: tuple[float, float] | None  # (over, under) at 2.5
    ah: tuple[float, float, float] | None  # (home-perspective line, home, away)


@dataclass(frozen=True)
class MatchMarkets:
    """One finished match with the odds the backtest needs."""

    match_id: int
    league: str
    season: str
    kickoff: float  # UTC epoch seconds
    home: int
    away: int
    home_goals: int
    away_goals: int
    ou_open: tuple[float, float] | None  # odds_source (over, under)
    ah_open: tuple[float, float, float] | None  # odds_source (line, home, away)
    closing_ou: ClosingOdds | None
    closing_ah: ClosingOdds | None
    neutral: bool = False  # neutral venue (internationals, docs/09)


@dataclass(frozen=True)
class SeasonTask:
    league: str
    season: str
    history: LeagueMatches  # all finished matches of the league (fit_league filters by time)
    matches: tuple[MatchMarkets, ...]  # matches of this season to price
    fit: FitSettings
    blend_weights: tuple[float, ...]
    refit_days: int
    max_goals: int


@dataclass(frozen=True)
class MatchPrice:
    """Model probabilities for one match and blend weight (p_win of each side)."""

    match_id: int
    blend_weight: float
    low_confidence: bool
    p_over: float
    p_under: float
    p_ah_home: float | None  # None when the opening AH line is not a half line / missing
    p_ah_away: float | None


@dataclass
class SeasonPrices:
    league: str
    season: str
    xi_per_day: float
    prices: list[MatchPrice] = field(default_factory=list)
    n_blocks: int = 0
    n_fits_retried: int = 0
    skipped_unknown_team: int = 0
    ah_non_half: int = 0
    skipped_absurd: list[str] = field(default_factory=list)


def season_blocks(kickoffs: Iterable[float], refit_days: int) -> list[tuple[float, float]]:
    """[start, end) blocks from the first kickoff's UTC midnight until the last kickoff."""
    ks = list(kickoffs)
    if not ks:
        return []
    step = refit_days * SECONDS_PER_DAY
    start = float(np.floor(min(ks) / SECONDS_PER_DAY) * SECONDS_PER_DAY)
    last = max(ks)
    blocks = []
    while start <= last:
        blocks.append((start, start + step))
        start += step
    return blocks


def price_season(task: SeasonTask) -> SeasonPrices:
    out = SeasonPrices(task.league, task.season, task.fit.xi_per_day)
    for start, end in season_blocks((m.kickoff for m in task.matches), task.refit_days):
        block = [m for m in task.matches if start <= m.kickoff < end]
        if not block:
            continue
        # docs/03 §10.1: predicted matches lie in [d, d + refit); training (asserted in
        # fit_league) is strictly before d.
        assert all(start <= m.kickoff < end for m in block), "prediction outside block"
        model = fit_league(task.history, start, task.fit)
        out.n_blocks += 1
        out.n_fits_retried += int(model.goals.retried) + int(bool(model.xg and model.xg.retried))
        for m in block:
            if not (model.has_team(m.home) and model.has_team(m.away)):
                out.skipped_unknown_team += 1
                continue
            low = m.home in model.low_confidence or m.away in model.low_confidence
            ah_line = m.ah_open[0] if m.ah_open else None
            ah_half = ah_line is not None and classify_line(ah_line) == "half"
            if ah_line is not None and not ah_half:
                out.ah_non_half += 1
            for w in task.blend_weights:
                try:
                    mat = model.matrix(m.home, m.away, w, task.max_goals, m.neutral)
                except AbsurdRatesError as exc:
                    # The model cannot price this match sensibly (e.g. weakly connected
                    # international sides). Never bet on it: skip, count, list in report.
                    out.skipped_absurd.append(
                        f"{task.league} {task.season} match {m.match_id} "
                        f"(xi={task.fit.xi_per_day}, w={w}): {exc}"
                    )
                    continue
                p_home = p_away = None
                if ah_half and ah_line is not None:
                    p_home = price_selection(mat, "AH", "home", ah_line).p_win
                    p_away = price_selection(mat, "AH", "away", ah_line).p_win
                out.prices.append(
                    MatchPrice(
                        match_id=m.match_id,
                        blend_weight=w,
                        low_confidence=low,
                        p_over=price_selection(mat, "OU", "over", OU_LINE).p_win,
                        p_under=price_selection(mat, "OU", "under", OU_LINE).p_win,
                        p_ah_home=p_home,
                        p_ah_away=p_away,
                    )
                )
    return out


def run_tasks(
    warn: Callable[[str], None], tasks: Sequence[SeasonTask], workers: int | None = None
) -> list[SeasonPrices]:
    """Fit/price every task; local CPU parallelism (docs/03 §10.3), results in task order.

    If the Windows process pool breaks (seen once as WinError 6), the batch is re-run
    in-process after a warning. Model errors inside a task are raised, never retried.
    """
    n = workers or max(1, (os.cpu_count() or 2) - 1)
    if n == 1 or len(tasks) == 1:
        return [price_season(t) for t in tasks]
    try:
        with ProcessPoolExecutor(max_workers=n) as pool:
            return list(pool.map(price_season, tasks))
    except BrokenProcessPool as exc:
        warn(f"process pool broke ({exc}); re-running {len(tasks)} tasks in-process")
        return [price_season(t) for t in tasks]


# ---------------------------------------------------------------------------
# Value pipeline + settlement over the prices
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValueParams:
    shrink_weight: float
    min_edge: float
    min_odds: float
    max_odds: float
    max_model_market_gap: float


@dataclass(frozen=True)
class SelectionRecord:
    league: str
    season: str
    match_id: int
    kickoff: float
    market: str
    line: float
    selection: str
    canonical: bool  # over / home: one row per market for log loss, ECE
    odds: float
    p_model: float
    p_market_devig: float
    p_final: float
    expected_multiplier: float
    edge: float
    qualifies: bool
    reasons: tuple[str, ...]
    won: int  # half lines: 1 if the selection won
    return_multiplier: float  # at `odds`, per unit stake
    closing_odds: float | None  # same line only
    closing_source: str | None
    p_close_devig: float | None


def _closing_for(
    market: str, sel_index: int, line: float, m: MatchMarkets
) -> tuple[float | None, str | None, float | None]:
    if market == "OU":
        c = m.closing_ou
        if c is None or c.ou is None:
            return None, None, None
        pair = c.ou
    else:
        c = m.closing_ah
        if c is None or c.ah is None or abs(c.ah[0] - line) > 1e-9:
            return None, None, None  # closing line moved: CLV undefined at our line
        pair = (c.ah[1], c.ah[2])
    return pair[sel_index], c.source, devig_pair(*pair)[sel_index]


def evaluate(
    prices: Iterable[MatchPrice],
    markets: dict[int, MatchMarkets],
    blend_weight: float,
    vp: ValueParams,
) -> list[SelectionRecord]:
    records: list[SelectionRecord] = []
    for p in prices:
        if p.blend_weight != blend_weight:
            continue
        m = markets[p.match_id]
        pairs: list[
            tuple[str, float, tuple[str, str], tuple[float, float], tuple[float, float]]
        ] = []
        if m.ou_open is not None:
            pairs.append(("OU", OU_LINE, ("over", "under"), (p.p_over, p.p_under), m.ou_open))
        if m.ah_open is not None and p.p_ah_home is not None and p.p_ah_away is not None:
            pairs.append(
                ("AH", m.ah_open[0], ("home", "away"), (p.p_ah_home, p.p_ah_away), m.ah_open[1:])
            )
        for market, line, sels, pm, odds in pairs:
            probs = (
                OutcomeProbs(pm[0], 0, 0, 0, 1 - pm[0]),
                OutcomeProbs(pm[1], 0, 0, 0, 1 - pm[1]),
            )
            legs = evaluate_pair(
                line,
                sels,
                probs,
                odds,
                low_confidence=p.low_confidence,
                minutes_to_kickoff=None,
                stale_prediction=False,
                shrink_weight=vp.shrink_weight,
                min_edge=vp.min_edge,
                min_odds=vp.min_odds,
                max_odds=vp.max_odds,
                max_model_market_gap=vp.max_model_market_gap,
            )
            for k, leg in enumerate(legs):
                result = settle(market, leg.selection, line, m.home_goals, m.away_goals)  # type: ignore[arg-type]
                c_odds, c_src, c_dev = _closing_for(market, k, line, m)
                records.append(
                    SelectionRecord(
                        league=m.league,
                        season=m.season,
                        match_id=m.match_id,
                        kickoff=m.kickoff,
                        market=market,
                        line=line,
                        selection=leg.selection,
                        canonical=k == 0,
                        odds=leg.odds,
                        p_model=leg.p_model,
                        p_market_devig=leg.p_market_devig,
                        p_final=leg.p_final,
                        expected_multiplier=leg.expected_multiplier,
                        edge=leg.edge,
                        qualifies=leg.qualifies,
                        reasons=leg.reasons,
                        won=int(result == "win"),
                        return_multiplier=result_multiplier(result, leg.odds),
                        closing_odds=c_odds,
                        closing_source=c_src,
                        p_close_devig=c_dev,
                    )
                )
    return records
