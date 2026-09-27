"""docs/05 §7 slip builder tests — daily 2-odds part (mid/mega come in Phase 4)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from engine.config import get_settings
from engine.slips.builder import best_daily_2odds, daily_2odds
from engine.slips.constraints import Leg

NOW = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
CFG = get_settings().slips.daily_2odds


def leg(
    match_id: int,
    odds: float,
    p: float,
    *,
    qualifies: bool = True,
    market: str = "OU",
    hours: float = 6.0,
) -> Leg:
    return Leg(
        match_id=match_id,
        market=market,
        line=2.5,
        selection="under",
        odds=odds,
        p_final=p,
        expected_multiplier=p * odds,
        kickoff_utc=NOW + timedelta(hours=hours),
        qualifies=qualifies,
    )


A, B, C = leg(1, 1.40, 0.76), leg(2, 1.42, 0.75), leg(3, 1.35, 0.78)


def test_worked_example_returns_a_plus_c() -> None:
    chosen = best_daily_2odds([A, B, C], CFG)
    assert chosen is not None
    assert {lg.match_id for lg in chosen.legs} == {1, 3}
    assert chosen.totals.total_odds == 1.40 * 1.35
    assert abs(chosen.totals.p_all_win - 0.5928) < 1e-12


def test_one_leg_per_match() -> None:
    same_match = [leg(1, 1.40, 0.76), leg(1, 1.35, 0.78, market="AH"), leg(2, 1.42, 0.75)]
    chosen = best_daily_2odds(same_match, CFG)
    assert chosen is not None
    assert len({lg.match_id for lg in chosen.legs}) == len(chosen.legs)


def test_non_qualifying_and_low_probability_legs_never_used() -> None:
    flagged = leg(4, 1.37, 0.95, qualifies=False)
    weak = leg(5, 1.38, 0.59)
    chosen = best_daily_2odds([A, B, C, flagged, weak], CFG)
    assert chosen is not None
    assert all(lg.qualifies and lg.p_final >= CFG.min_leg_probability for lg in chosen.legs)


def test_totals_within_target_and_edge_threshold() -> None:
    rng = random.Random(1)
    pool = [
        leg(i, round(rng.uniform(1.25, 1.9), 2), round(rng.uniform(0.6, 0.8), 3)) for i in range(25)
    ]
    chosen = best_daily_2odds(pool, CFG)
    assert chosen is not None
    assert CFG.target_odds_min <= chosen.totals.total_odds <= CFG.target_odds_max
    assert chosen.totals.edge >= CFG.min_slip_edge


def test_empty_or_infeasible_pool_gives_no_slip() -> None:
    assert best_daily_2odds([], CFG) is None
    assert best_daily_2odds([leg(1, 1.05, 0.9), leg(2, 1.05, 0.9)], CFG) is None  # odds too low


def test_deterministic_under_shuffle() -> None:
    rng = random.Random(7)
    pool = [
        leg(i, round(rng.uniform(1.25, 1.9), 2), round(rng.uniform(0.6, 0.8), 2)) for i in range(30)
    ]
    expected = best_daily_2odds(pool, CFG)
    for seed in range(5):
        shuffled = pool[:]
        random.Random(seed).shuffle(shuffled)
        assert best_daily_2odds(shuffled, CFG) == expected


def test_window_excludes_too_soon_and_too_late() -> None:
    soon = leg(1, 1.40, 0.76, hours=0.2)  # 12 min away
    late = leg(3, 1.35, 0.78, hours=25)
    chosen = daily_2odds([soon, B, late, leg(6, 1.36, 0.77)], NOW, CFG)
    assert chosen is not None
    assert {lg.match_id for lg in chosen.legs} == {2, 6}


def test_selection_cap_limits_leg_count() -> None:
    pool = [leg(i, 1.28, 0.8) for i in range(6)]  # 3 legs ≈ 2.10 is feasible
    three = best_daily_2odds(pool, CFG)
    assert three is not None and len(three.legs) == 3
    assert best_daily_2odds(pool, CFG, max_selections=2) is None  # 2 legs: 1.64 < 1.85


# ---- mid accumulator (docs/05 §5, skip rule per user decision 2026-09-27) ----------

from dataclasses import replace  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from engine.slips.builder import mega_acca, mid_acca, weekend_window  # noqa: E402

MID = get_settings().slips.mid_acca
MEGA = get_settings().slips.mega_acca
VAL = get_settings().value
LAGOS = ZoneInfo("Africa/Lagos")


def test_mid_acca_skips_a_leg_that_breaks_the_edge_and_continues() -> None:
    good = [leg(i, 1.60, 0.66) for i in range(1, 6)]  # edge +5.6% each
    # tie-break puts the high-p but negative-EM leg first; it must be skipped, not stop
    bad = replace(leg(9, 1.30, 0.70), expected_multiplier=0.20)
    cfg = MID.model_copy(update={"min_slip_edge": 0.0})
    chosen = mid_acca([bad, *good], NOW, cfg)
    assert chosen is not None
    assert [lg.match_id for lg in chosen.legs] == [1, 2, 3, 4, 5]
    assert chosen.totals.edge >= 0.0


def test_mid_acca_needs_min_legs_and_uses_one_leg_per_match() -> None:
    four = [leg(i, 1.60, 0.66) for i in range(4)]
    assert mid_acca(four, NOW, MID) is None  # min_legs 5: no forcing
    dup = [*four, leg(3, 1.55, 0.67, market="AH"), leg(7, 1.6, 0.66)]
    chosen = mid_acca(dup, NOW, MID)
    assert chosen is not None and len({lg.match_id for lg in chosen.legs}) == len(chosen.legs)
    assert next(lg for lg in chosen.legs if lg.match_id == 3).market == "AH"  # higher p wins


def test_mid_acca_respects_max_legs_window_and_cap() -> None:
    many = [leg(i, 1.55, 0.67) for i in range(20)]
    chosen = mid_acca(many, NOW, MID)
    assert chosen is not None and len(chosen.legs) == MID.max_legs
    assert mid_acca(many, NOW, MID, max_selections=6) is not None
    assert len(mid_acca(many, NOW, MID, max_selections=6).legs) == 6  # type: ignore[union-attr]
    late = [leg(i, 1.55, 0.67, hours=80) for i in range(20)]
    assert mid_acca(late, NOW, MID) is None


# ---- mega accumulator (docs/05 §6) ----------------------------------------------


def test_weekend_window_current_next_and_clipped() -> None:
    sunday = datetime(2026, 9, 27, 13, 50, tzinfo=UTC)  # Sun 14:50 Lagos, inside
    s, e = weekend_window(sunday, "Fri 18:00", "Mon 02:00", LAGOS)
    assert s == sunday + timedelta(minutes=15)
    assert e == datetime(2026, 9, 28, 2, 0, tzinfo=LAGOS)
    monday = datetime(2026, 9, 28, 3, 0, tzinfo=LAGOS)  # just after: next weekend
    s, e = weekend_window(monday, "Fri 18:00", "Mon 02:00", LAGOS)
    assert (s, e) == (
        datetime(2026, 10, 2, 18, 0, tzinfo=LAGOS),
        datetime(2026, 10, 5, 2, 0, tzinfo=LAGOS),
    )
    wednesday = datetime(2026, 9, 30, 12, 0, tzinfo=LAGOS)
    assert weekend_window(wednesday, "Fri 18:00", "Mon 02:00", LAGOS)[0] == datetime(
        2026, 10, 2, 18, 0, tzinfo=LAGOS
    )


def test_mega_respects_max_legs_cap_and_filters() -> None:
    window = (NOW, NOW + timedelta(days=3))
    pool = [leg(i, 1.30, 0.80) for i in range(60)]
    chosen = mega_acca(pool, window, MEGA, VAL.min_odds, VAL.max_odds)
    assert chosen is not None and len(chosen.legs) == MEGA.max_legs == 30
    capped = mega_acca(
        pool, window, MEGA.model_copy(update={"max_legs": 80}), VAL.min_odds, VAL.max_odds
    )
    assert capped is not None and len(capped.legs) == 50  # SportyBet betslip limit
    flagged = [replace(lg, sanity_ok=False) for lg in pool]
    assert mega_acca(flagged, window, MEGA, VAL.min_odds, VAL.max_odds) is None
    low_p = [leg(i, 1.30, 0.64) for i in range(5)]
    assert mega_acca(low_p, window, MEGA, VAL.min_odds, VAL.max_odds) is None
    one = [leg(1, 1.30, 0.80)]
    assert mega_acca(one, window, MEGA, VAL.min_odds, VAL.max_odds) is None  # needs >= 2


def test_mega_is_deterministic_under_shuffle() -> None:
    rng = random.Random(3)
    window = (NOW, NOW + timedelta(days=3))
    pool = [
        leg(i, round(rng.uniform(1.2, 1.6), 2), round(rng.uniform(0.65, 0.85), 3))
        for i in range(40)
    ]
    expected = mega_acca(pool, window, MEGA, VAL.min_odds, VAL.max_odds)
    shuffled = pool[:]
    random.Random(1).shuffle(shuffled)
    assert mega_acca(shuffled, window, MEGA, VAL.min_odds, VAL.max_odds) == expected
