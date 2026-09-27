"""Slip builders (docs/05 §3-6). Pure: legs in, chosen legs out.

Every builder returns None rather than relax a threshold ("no forcing", docs/05 §3).
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from engine.config import Daily2OddsCfg, MegaAccaCfg, MidAccaCfg
from engine.slips.constraints import (
    MIN_LEAD,
    SPORTYBET_MAX_SELECTIONS,
    Leg,
    SlipTotals,
    distinct_matches,
    in_window,
    slip_totals,
    sort_legs,
    tie_break_key,
)

# docs/05 §4: candidate pool is capped to the best 60 legs by tie-break order.
DAILY_POOL_CAP = 60


@dataclass(frozen=True)
class ChosenSlip:
    legs: tuple[Leg, ...]
    totals: SlipTotals


def best_daily_2odds(
    legs: Sequence[Leg], cfg: Daily2OddsCfg, max_selections: int | None = None
) -> ChosenSlip | None:
    """Pick the feasible combination with the highest p_all_win (docs/05 §4).

    Window filtering is the caller's job (`daily_2odds` for live, per-day groups
    in the backtest). Returns None when nothing is feasible — never relaxes.
    """
    pool = sort_legs(
        [leg for leg in legs if leg.qualifies and leg.p_final >= cfg.min_leg_probability]
    )[:DAILY_POOL_CAP]
    max_legs = cfg.max_legs if max_selections is None else min(cfg.max_legs, max_selections)

    best: tuple[tuple[object, ...], ChosenSlip] | None = None
    for size in range(cfg.min_legs, max_legs + 1):
        for combo in itertools.combinations(pool, size):
            if not distinct_matches(combo):
                continue
            t = slip_totals(combo)
            if not (cfg.target_odds_min <= t.total_odds <= cfg.target_odds_max):
                continue
            if t.edge < cfg.min_slip_edge:
                continue
            # Higher p_all_win, then higher edge, then the legs' tie-break order.
            key = (-t.p_all_win, -t.edge, tuple(tie_break_key(leg) for leg in combo))
            if best is None or key < best[0]:
                best = (key, ChosenSlip(tuple(combo), t))
    return None if best is None else best[1]


def daily_2odds(
    legs: Sequence[Leg], now: datetime, cfg: Daily2OddsCfg, max_selections: int | None = None
) -> ChosenSlip | None:
    """Live daily slip: kickoffs from now + 15 min to now + window_hours."""
    start, end = now + MIN_LEAD, now + timedelta(hours=cfg.window_hours)
    return best_daily_2odds(
        [leg for leg in legs if in_window(leg, start, end)], cfg, max_selections
    )


def best_leg_per_match(legs: Sequence[Leg]) -> list[Leg]:
    """One leg per match: the best by tie-break order; result sorted by tie-break order."""
    best: dict[int, Leg] = {}
    for leg in sort_legs(legs):
        best.setdefault(leg.match_id, leg)
    return sort_legs(list(best.values()))


MID_WINDOW = timedelta(hours=72)  # docs/05 §5


def mid_acca(
    legs: Sequence[Leg],
    now: datetime,
    cfg: MidAccaCfg,
    max_selections: int = SPORTYBET_MAX_SELECTIONS,
) -> ChosenSlip | None:
    """docs/05 §5, greedy in tie-break order. A leg that would take the running slip edge
    below min_slip_edge is skipped and the next one tried (user decision 2026-09-27)."""
    start, end = now + MIN_LEAD, now + MID_WINDOW
    pool = best_leg_per_match(
        [
            leg
            for leg in legs
            if leg.qualifies
            and leg.p_final >= cfg.min_leg_probability
            and in_window(leg, start, end)
        ]
    )
    cap = min(cfg.max_legs, max_selections)
    chosen: list[Leg] = []
    for leg in pool:
        if len(chosen) >= cap:
            break
        if slip_totals([*chosen, leg]).edge >= cfg.min_slip_edge:
            chosen.append(leg)
    if len(chosen) < cfg.min_legs:
        return None
    return ChosenSlip(tuple(chosen), slip_totals(chosen))


_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _parse_weekday_time(spec: str) -> tuple[int, time]:
    day, hhmm = spec.split(" ")
    h, m = hhmm.split(":")
    return _DAYS.index(day), time(int(h), int(m))


def weekend_window(
    now: datetime, start_spec: str, end_spec: str, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """The current weekend window if `now` is inside one, else the next one (docs/05 §6).

    Specs are local (Lagos) weekday + time, e.g. "Fri 18:00" .. "Mon 02:00"; the start
    is clipped to now + 15 min. Returned datetimes are timezone-aware.
    """
    local = now.astimezone(tz)
    sd, st = _parse_weekday_time(start_spec)
    ed, et = _parse_weekday_time(end_spec)
    length_days = (ed - sd) % 7
    # most recent start at or before now
    back = (local.weekday() - sd) % 7
    start = datetime.combine(local.date() - timedelta(days=back), st, tzinfo=tz)
    if start > local:
        start -= timedelta(days=7)
    end = datetime.combine(start.date() + timedelta(days=length_days), et, tzinfo=tz)
    if end <= start:
        end += timedelta(days=7)
    if local >= end:  # past this weekend: the next one
        start += timedelta(days=7)
        end += timedelta(days=7)
    return max(start, local + MIN_LEAD), end


def mega_acca(
    legs: Sequence[Leg],
    window: tuple[datetime, datetime],
    cfg: MegaAccaCfg,
    min_odds: float,
    max_odds: float,
    max_selections: int = SPORTYBET_MAX_SELECTIONS,
) -> ChosenSlip | None:
    """docs/05 §6: top legs by tie-break order in the weekend window, ≥ 2 legs."""
    start, end = window
    pool = best_leg_per_match(
        [
            leg
            for leg in legs
            if leg.sanity_ok
            and leg.edge >= cfg.min_leg_edge
            and leg.p_final >= cfg.min_leg_probability
            and min_odds <= leg.odds <= max_odds
            and in_window(leg, start, end)
        ]
    )
    chosen = pool[: min(cfg.max_legs, max_selections)]
    if len(chosen) < 2:
        return None
    return ChosenSlip(tuple(chosen), slip_totals(chosen))
