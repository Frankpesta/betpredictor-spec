"""SportyBet parser on saved discovery payloads (docs/04 §2.4: one test per fact F3–F9)."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from engine.sportybet import markets as mk

FIX = Path(__file__).parent / "fixtures" / "sportybet"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / name).read_text(encoding="utf-8"))
    return data


EVENT = _load("event_arsenal_leeds.json")  # factsCenter/event, many market types
PC = _load("pcEvents_epl_with_ah.json")  # factsCenter/pcEvents with marketId 1,18,16
# docs/discovered/sportybet/2026-10-08: pcEvents with 1,10,19,20,29 (trimmed to one event)
NEW = _load("pcEvents_epl_new_markets.json")


def _odds(parsed: mk.ParsedEvents) -> dict[tuple[str, float, str], mk.SbOdds]:
    (ev,) = parsed.events
    return {(o.market, o.line, o.selection): o for o in ev.odds}


def test_f3_event_id_format() -> None:
    evs = mk.parse_pc_events(PC).events
    assert [e.event_id for e in evs] == ["sr:match:72221292", "sr:match:72221294"]
    assert all(e.tournament_id == "sr:tournament:17" for e in evs)


def test_f4_teams_and_kickoff_utc() -> None:
    (ev,) = mk.parse_event_detail(EVENT).events
    assert (ev.home, ev.away) == ("Arsenal", "Leeds United")
    # page shows "Saturday 10/10 12:30" in GMT+1 (Lagos) == 11:30 UTC
    assert ev.kickoff_utc == datetime(2026, 10, 10, 11, 30, tzinfo=UTC)


def test_f5_only_two_way_asian_handicap_market_16() -> None:
    parsed = mk.parse_event_detail(EVENT)
    ah = {o for o in parsed.events[0].odds if o.market == "AH"}
    assert ah and all(o.sb_market_id == "16" for o in ah)
    # market 14 (3-way European handicap), 66 (1st half AH), 68 (1st half O/U),
    # 19 (team total) and 1 (1X2) were present and skipped
    assert parsed.skipped["other_market"] > 0
    assert not any(o.sb_specifier.startswith("hcp=") and ":" in o.sb_specifier for o in ah)


def test_f6_over_under_market_18_full_time() -> None:
    odds = _odds(mk.parse_event_detail(EVENT))
    over, under = odds[("OU", 2.5, "over")], odds[("OU", 2.5, "under")]
    assert (over.odds, under.odds) == (1.68, 2.15)
    assert (over.sb_market_id, over.sb_specifier) == ("18", "total=2.5")
    # team-total market 19 has an "Over 2.5" at 2.65 — it must not leak into match goals
    assert all(o.sb_market_id == "18" for o in odds.values() if o.market == "OU")
    assert odds[("OU_HOME", 2.5, "over")].sb_market_id == "19"
    assert ("OU", 2.0, "over") in odds  # whole lines are parsed (flagged later, docs/05)


def test_f7_outcome_ids() -> None:
    odds = _odds(mk.parse_event_detail(EVENT))
    assert odds[("AH", -0.5, "home")].sb_outcome_id == "1714"
    assert odds[("AH", -0.5, "away")].sb_outcome_id == "1715"
    assert odds[("OU", 2.5, "over")].sb_outcome_id == "12"
    assert odds[("OU", 2.5, "under")].sb_outcome_id == "13"


def _set_active(
    payload: dict[str, Any],
    market_id: str,
    spec: str,
    *,
    outcome_active: int | None = None,
    market_status: int | None = None,
) -> dict[str, Any]:
    p = copy.deepcopy(payload)
    for m in p["data"]["markets"]:
        if m["id"] == market_id and m.get("specifier") == spec:
            if market_status is not None:
                m["status"] = market_status
            if outcome_active is not None:
                m["outcomes"][0]["isActive"] = outcome_active
    return p


def test_f8_suspended_outcomes_and_markets_are_discarded() -> None:
    susp_outcome = mk.parse_event_detail(_set_active(EVENT, "18", "total=2.5", outcome_active=0))
    odds = _odds(susp_outcome)
    assert ("OU", 2.5, "over") not in odds and ("OU", 2.5, "under") not in odds  # pair broken
    assert susp_outcome.skipped["inactive"] == 1 and susp_outcome.skipped["missing_pair"] == 1
    susp_market = mk.parse_event_detail(_set_active(EVENT, "16", "hcp=-0.5", market_status=2))
    assert ("AH", -0.5, "home") not in _odds(susp_market)
    assert susp_market.skipped["inactive"] == 2


def test_f9_handicap_is_home_perspective_no_flip() -> None:
    """Rendered page: 'Home (-0.5) 1.41 / Away (+0.5) 2.80' for Arsenal (home favourite)."""
    odds = _odds(mk.parse_event_detail(EVENT))
    home, away = odds[("AH", -0.5, "home")], odds[("AH", -0.5, "away")]
    assert (home.odds, away.odds) == (1.41, 2.80)
    assert home.line == away.line == -0.5  # both carry the home-perspective line
    plus = odds[("AH", 0.5, "home")]
    assert plus.odds == 1.11  # "Home (+0.5)"


def test_listing_with_market_16_returns_all_ah_lines() -> None:
    parsed = mk.parse_pc_events(PC)
    ev = parsed.events[0]
    lines = sorted({o.line for o in ev.odds if o.market == "AH"})
    # hcp=-3.5 is in the payload with market status 2 (not offered) -> dropped (F8)
    assert lines == [-2.5, -1.5, -0.5, 0.5]
    assert parsed.skipped["inactive"] >= 2


def test_bounds_and_overround_filters() -> None:
    p = copy.deepcopy(EVENT)
    for m in p["data"]["markets"]:
        if m["id"] == "18" and m["specifier"] == "total=3.5":
            m["outcomes"][0]["odds"] = "1.20"  # 1/1.20 + 1/1.46 = 1.52 -> bad overround
        if m["id"] == "18" and m["specifier"] == "total=1":
            m["outcomes"][0]["odds"] = "1.01"  # at the floor -> discarded
    parsed = mk.parse_event_detail(p)
    odds = _odds(parsed)
    assert ("OU", 3.5, "over") not in odds and parsed.skipped["bad_overround"] == 2
    assert ("OU", 1.0, "over") not in odds and parsed.skipped["odds_too_low"] == 1
    for (market, line, _), _o in odds.items():
        if market not in ("AH", "OU"):
            continue
        lo, hi = mk.AH_LINE_RANGE if market == "AH" else mk.OU_LINE_RANGE
        assert lo <= line <= hi
        pair = [odds.get((market, line, s)) for s in mk.PAIRS[market]]
        assert all(pair)
        assert 1.0 <= sum(1 / x.odds for x in pair if x) <= 1.15


def test_out_of_range_lines_discarded() -> None:
    p = copy.deepcopy(EVENT)
    m = next(m for m in p["data"]["markets"] if m["id"] == "18" and m["specifier"] == "total=5.5")
    m["specifier"] = "total=6.5"
    parsed = mk.parse_event_detail(p)
    assert ("OU", 6.5, "over") not in _odds(parsed)
    # the sample's own 1st-half AH lines beyond ±0.5 are out of range too (docs/10 §4)
    before = mk.parse_event_detail(EVENT).skipped["line_out_of_range"]
    assert parsed.skipped["line_out_of_range"] == before + 2


def test_reused_market_id_with_other_name_is_refused() -> None:
    p = copy.deepcopy(EVENT)
    for m in p["data"]["markets"]:
        if m["id"] == "16":
            m["desc"] = "Handicap 0:1"
    parsed = mk.parse_event_detail(p)
    assert not any(o.market == "AH" for o in parsed.events[0].odds)
    assert parsed.skipped["unexpected_market_desc"] > 0


def test_error_envelopes_and_non_json_raise() -> None:
    with pytest.raises(mk.SportyBetPayloadError, match="bizCode"):
        mk.parse_pc_events({"bizCode": 19000, "message": "blocked", "data": None})
    with pytest.raises(mk.SportyBetPayloadError, match="bot check"):
        mk.load_json(b"<html>Just a moment...</html>")


def test_live_events_are_skipped() -> None:
    p = copy.deepcopy(PC)
    p["data"][0]["events"][0]["status"] = 1
    parsed = mk.parse_pc_events(p)
    assert len(parsed.events) == 1 and parsed.skipped["not_prematch"] == 1


def test_f11_share_code() -> None:
    share = _load("orders_share.json")
    assert mk.share_code(share["response"]) == "X6YP10"
    sel = share["request"]["selections"][0]
    assert set(sel) == {"eventId", "marketId", "specifier", "outcomeId"}


def test_new_markets_2026_10_08() -> None:
    """docs/discovered/sportybet/2026-10-08/new-markets.md: ids, outcomes, no specifier."""
    odds = _odds(mk.parse_pc_events(NEW))
    assert odds[("1X2", 0.0, "home")].odds == 1.44
    assert odds[("1X2", 0.0, "draw")].sb_outcome_id == "2"
    assert odds[("1X2", 0.0, "away")].odds == 7.81
    assert odds[("DC", 0.0, "home_draw")].sb_outcome_id == "9"
    assert odds[("DC", 0.0, "home_away")].sb_outcome_id == "10"
    assert odds[("DC", 0.0, "draw_away")].odds == 2.70
    assert (odds[("BTTS", 0.0, "yes")].odds, odds[("BTTS", 0.0, "no")].odds) == (2.10, 1.74)
    assert odds[("BTTS", 0.0, "yes")].sb_specifier == ""  # lineless: booked without specifier
    home = odds[("OU_HOME", 1.5, "over")]
    assert (home.odds, home.sb_market_id, home.sb_specifier) == (1.58, "19", "total=1.5")
    away = odds[("OU_AWAY", 0.5, "under")]
    assert (away.odds, away.sb_market_id, away.sb_outcome_id) == (1.97, "20", "13")
    # Leeds over 2.5 @14.00 / under @1.02: 1/14 + 1/1.02 = 1.052 -> kept
    assert ("OU_AWAY", 2.5, "under") in odds


def test_team_goals_desc_must_name_the_team() -> None:
    p = copy.deepcopy(NEW)
    ev = p["data"][0]["events"][0]
    for m in ev["markets"]:
        if m["id"] == "19":
            m["desc"] = "Leeds United Over/Under"  # the away team under the home id: refuse
    parsed = mk.parse_pc_events(p)
    assert not any(o.market == "OU_HOME" for o in parsed.events[0].odds)
    assert parsed.skipped["unexpected_market_desc"] == 5


def test_team_goals_short_label_is_accepted() -> None:
    """2026-10-08: "Nottingham Over/Under" for Nottingham Forest — side comes from the id."""
    p = copy.deepcopy(NEW)
    ev = p["data"][0]["events"][0]
    for m in ev["markets"]:
        if m["id"] == "20":
            m["desc"] = "Leeds Over/Under"
    odds = _odds(mk.parse_pc_events(p))
    assert odds[("OU_AWAY", 0.5, "under")].odds == 1.97


def test_double_chance_overround_is_scaled() -> None:
    p = copy.deepcopy(NEW)
    ev = p["data"][0]["events"][0]
    dc = next(m for m in ev["markets"] if m["id"] == "10")
    dc["outcomes"][0]["odds"] = "1.01"  # at the floor: the whole 3-way group is incomplete
    parsed = mk.parse_pc_events(p)
    assert not any(o.market == "DC" for o in parsed.events[0].odds)
    assert parsed.skipped["odds_too_low"] == 1 and parsed.skipped["missing_pair"] == 2


HT = _load("pcEvents_epl_halftime.json")  # docs/discovered/sportybet/2026-10-08 (half-time ids)


def test_half_time_markets_2026_10_08() -> None:
    parsed = mk.parse_pc_events(HT)
    odds = _odds(parsed)
    assert odds[("AH_1H", -0.5, "home")].odds == 1.91  # Arsenal 1H -0.5 (home line, like 16)
    assert odds[("AH_1H", -0.5, "away")].sb_outcome_id == "1715"
    assert odds[("1X2_1H", 0.0, "home")].odds == 1.89
    assert odds[("1X2_1H", 0.0, "draw")].sb_market_id == "60"
    assert odds[("DC_2H", 0.0, "home_draw")].sb_market_id == "85"
    assert odds[("OU_1H", 0.5, "over")].sb_specifier == "total=0.5"
    assert odds[("OU_2H", 2.5, "under")].sb_market_id == "90"
    assert odds[("OU_HOME_1H", 0.5, "over")].sb_market_id == "69"
    assert odds[("OU_AWAY_2H", 0.5, "under")].sb_market_id == "92"
    assert odds[("BTTS_1H", 0.0, "yes")].sb_outcome_id == "74"
    # untested lines are out of range; 2H GG/NG (95) failed the gate and is not parsed
    assert not any(k[0].startswith("OU_") and k[0].endswith("H") and k[1] > 2.5 for k in odds)
    assert not any(k[0] in ("OU_HOME_1H", "OU_AWAY_1H") and k[1] != 0.5 for k in odds)
    assert not any(k[0] == "AH_1H" and abs(k[1]) > 0.5 for k in odds)
    assert not any(k[0] == "BTTS_2H" for k in odds)
    assert parsed.skipped["other_market"] >= 1


def test_full_time_team_goals_refuse_half_time_desc() -> None:
    assert not mk.MARKET_SPECS["19"].desc_ok("1st half - Arsenal Over/Under", "Arsenal", "Leeds")
    assert mk.MARKET_SPECS["69"].desc_ok(
        "1st half - Nottingham Over/Under", "Nottingham Forest", "Arsenal"
    )
    assert not mk.MARKET_SPECS["69"].desc_ok("1st half - Leeds Over/Under", "Arsenal", "Leeds")
