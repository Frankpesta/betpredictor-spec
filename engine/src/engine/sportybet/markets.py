"""SportyBet payload parsing and normalisation (docs/04 §2.3). Pure.

Every id and convention here is recorded in
docs/discovered/sportybet/2026-09-27/endpoints.md (facts F3–F9):
- market "16" = full-time two-way Asian Handicap, specifier `hcp=<x>` is the HOME line
  (F9: "Home (-0.5)" / "Away (+0.5)" for hcp=-0.5), outcomes 1714 home / 1715 away;
- market "18" = full-time Over/Under, specifier `total=<x>`, outcomes 12 over / 13 under;
- market "14" (3-way European handicap) and 66/68 (1st half) are never used;
- docs/discovered/sportybet/2026-10-08/new-markets.md: "19"/"20" home/away team goals
  (`total=<x>`, desc "<team name> Over/Under", outcomes 12/13), "1" 1X2 (1/2/3),
  "10" double chance (9/10/11), "29" GG/NG (74 yes / 76 no) — no specifier;
- kickoff `estimateStartTime` is epoch milliseconds (UTC instant);
- active = market `status == 0`, outcome `isActive == 1`, not `banned`.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from engine.model.markets import split_period

OK_BIZ_CODE = 10000
AH_MARKET_ID = "16"
OU_MARKET_ID = "18"
HOME_GOALS_MARKET_ID = "19"
AWAY_GOALS_MARKET_ID = "20"
X12_MARKET_ID = "1"
DC_MARKET_ID = "10"
BTTS_MARKET_ID = "29"
AH_DESC_PREFIX = "Asian Handicap"
OU_DESC = "Over/Under"
TEAM_OU_SUFFIX = " Over/Under"


def _team_desc(team: str, other: str, desc: str, prefix: str = "") -> bool:
    """Team-goals desc is "<prefix><team label> Over/Under". The label is SportyBet's short
    name and can differ from the event's ("Nottingham" for "Nottingham Forest", "Milan" for
    "AC Milan", 2026-10-08), so the side comes from the market id; refuse only a desc naming
    the *other* team, plain match goals, or (full time) a half-time desc."""
    if not desc.startswith(prefix) or not desc.endswith(TEAM_OU_SUFFIX):
        return False
    label = desc[len(prefix) : -len(TEAM_OU_SUFFIX)]
    if not label or (not prefix and label.lower().startswith(("1st ", "2nd "))):
        return False
    return label != other or team == other


@dataclass(frozen=True)
class MarketSpec:
    code: str  # our market code (engine.model.markets.Market)
    specifier_key: str | None  # None: no line (stored as 0.0)
    outcomes: dict[str, str]  # SportyBet outcome id -> our selection, in group order
    desc_ok: Callable[[str, str, str], bool]  # (desc, home name, away name)


MARKET_SPECS: dict[str, MarketSpec] = {
    AH_MARKET_ID: MarketSpec(
        "AH",
        "hcp",
        {"1714": "home", "1715": "away"},
        lambda d, h, a: d.startswith(AH_DESC_PREFIX),
    ),
    OU_MARKET_ID: MarketSpec(
        "OU", "total", {"12": "over", "13": "under"}, lambda d, h, a: d == OU_DESC
    ),
    HOME_GOALS_MARKET_ID: MarketSpec(
        "OU_HOME", "total", {"12": "over", "13": "under"}, lambda d, h, a: _team_desc(h, a, d)
    ),
    AWAY_GOALS_MARKET_ID: MarketSpec(
        "OU_AWAY", "total", {"12": "over", "13": "under"}, lambda d, h, a: _team_desc(a, h, d)
    ),
    X12_MARKET_ID: MarketSpec(
        "1X2", None, {"1": "home", "2": "draw", "3": "away"}, lambda d, h, a: d == "1X2"
    ),
    DC_MARKET_ID: MarketSpec(
        "DC",
        None,
        {"9": "home_draw", "10": "home_away", "11": "draw_away"},
        lambda d, h, a: d == "Double Chance",
    ),
    BTTS_MARKET_ID: MarketSpec(
        "BTTS", None, {"74": "yes", "76": "no"}, lambda d, h, a: d == "GG/NG"
    ),
}


def _half_specs(half: str, ids: dict[str, str]) -> dict[str, MarketSpec]:
    """docs/10 §3 + docs/discovered/sportybet/2026-10-08/new-markets.md (pcEvents probe).
    `ids`: our full-time code -> SportyBet id for this half. 2H GG/NG (95) failed the
    half-time gate and is not parsed."""
    title = f"{half} Half - "
    team_prefix = "1st half - " if half == "1st" else "2nd Half - "
    p = "1H" if half == "1st" else "2H"
    return {
        ids["1X2"]: MarketSpec(
            f"1X2_{p}",
            None,
            {"1": "home", "2": "draw", "3": "away"},
            lambda d, h, a: d == title + "1X2",
        ),
        ids["DC"]: MarketSpec(
            f"DC_{p}",
            None,
            {"9": "home_draw", "10": "home_away", "11": "draw_away"},
            lambda d, h, a: d == title + "Double Chance",
        ),
        ids["AH"]: MarketSpec(
            f"AH_{p}",
            "hcp",
            {"1714": "home", "1715": "away"},
            lambda d, h, a: d == title + "Asian Handicap",
        ),
        ids["OU"]: MarketSpec(
            f"OU_{p}",
            "total",
            {"12": "over", "13": "under"},
            lambda d, h, a: d == title + "Over/Under",
        ),
        ids["OU_HOME"]: MarketSpec(
            f"OU_HOME_{p}",
            "total",
            {"12": "over", "13": "under"},
            lambda d, h, a: _team_desc(h, a, d, team_prefix),
        ),
        ids["OU_AWAY"]: MarketSpec(
            f"OU_AWAY_{p}",
            "total",
            {"12": "over", "13": "under"},
            lambda d, h, a: _team_desc(a, h, d, team_prefix),
        ),
        **(
            {
                ids["BTTS"]: MarketSpec(
                    f"BTTS_{p}",
                    None,
                    {"74": "yes", "76": "no"},
                    lambda d, h, a: d == title + "GG/NG",
                )
            }
            if "BTTS" in ids
            else {}
        ),
    }


MARKET_SPECS |= _half_specs(
    "1st",
    {
        "1X2": "60",
        "DC": "63",
        "AH": "66",
        "OU": "68",
        "OU_HOME": "69",
        "OU_AWAY": "70",
        "BTTS": "75",
    },
)
MARKET_SPECS |= _half_specs(
    "2nd", {"1X2": "83", "DC": "85", "AH": "88", "OU": "90", "OU_HOME": "91", "OU_AWAY": "92"}
)
MARKET_SPECS_BY_CODE = {sp.code: sp for sp in MARKET_SPECS.values()}
OUTCOMES: dict[str, dict[str, str]] = {mid: sp.outcomes for mid, sp in MARKET_SPECS.items()}
MARKET_CODE = {mid: sp.code for mid, sp in MARKET_SPECS.items()}
# selections of each market, in group order (two- or three-way)
PAIRS: dict[str, tuple[str, ...]] = {
    sp.code: tuple(sp.outcomes.values()) for sp in MARKET_SPECS.values()
}
PREMATCH_EVENT_STATUS = 0

# docs/04 §2.3 discard bounds.
MIN_ODDS_EXCLUSIVE = 1.01
AH_LINE_RANGE = (-3.5, 3.5)
OU_LINE_RANGE = (0.5, 5.5)
OVERROUND_RANGE = (1.00, 1.15)
GROUP_TOTAL = {"DC": 2.0, "DC_1H": 2.0, "DC_2H": 2.0}  # double chance sums to 2: scaled
# docs/10 §4: half-time lines limited to what the backtest checked
HALF_LINE_RANGES = {
    "OU": (0.5, 2.5),
    "OU_HOME": (0.5, 0.5),
    "OU_AWAY": (0.5, 0.5),
    "AH": (-0.5, 0.5),
}


class SportyBetPayloadError(ValueError):
    """The response is not the shape discovered (or bizCode signals an error)."""


@dataclass(frozen=True)
class SbOdds:
    market: str  # MARKET_SPECS codes: AH | OU | OU_HOME | OU_AWAY | 1X2 | DC | BTTS
    line: float  # AH: home perspective
    selection: str  # see engine.model.markets.SELECTIONS_BY_MARKET
    odds: float
    sb_market_id: str
    sb_specifier: str
    sb_outcome_id: str


@dataclass(frozen=True)
class SbEvent:
    event_id: str
    tournament_id: str
    tournament_name: str
    home: str
    away: str
    kickoff_utc: datetime
    odds: tuple[SbOdds, ...]


@dataclass
class ParsedEvents:
    events: list[SbEvent] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)  # reason -> count


def check_envelope(payload: dict[str, Any]) -> Any:
    if not isinstance(payload, dict) or payload.get("bizCode") != OK_BIZ_CODE:
        code = payload.get("bizCode") if isinstance(payload, dict) else None
        msg = payload.get("message") if isinstance(payload, dict) else None
        raise SportyBetPayloadError(f"bizCode {code!r} ({msg!r})")
    return payload.get("data")


def load_json(content: bytes) -> dict[str, Any]:
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # An HTML page here usually means a bot check: never try to get around it.
        raise SportyBetPayloadError(f"not JSON (possible bot check): {exc}") from exc
    if not isinstance(data, dict):
        raise SportyBetPayloadError("top-level JSON is not an object")
    return data


def parse_specifier(market_id: str, specifier: str | None) -> float | None:
    key = MARKET_SPECS[market_id].specifier_key
    if key is None:  # lineless market: no specifier expected
        return 0.0 if not specifier else None
    if not specifier or not specifier.startswith(key + "="):
        return None
    try:
        return float(specifier.split("=", 1)[1])
    except ValueError:
        return None


def _is_active(market: dict[str, Any], outcome: dict[str, Any]) -> bool:
    return (
        market.get("status") == 0
        and not market.get("banned", False)
        and outcome.get("isActive") == 1
    )


def _line_in_range(market: str, line: float) -> bool:
    if MARKET_SPECS_BY_CODE[market].specifier_key is None:
        return line == 0.0
    base, period = split_period(market)
    if period != "FT":
        lo, hi = HALF_LINE_RANGES[base]
        return lo <= line <= hi
    lo, hi = AH_LINE_RANGE if market == "AH" else OU_LINE_RANGE
    return lo <= line <= hi


def parse_markets(
    markets: list[dict[str, Any]],
    skipped: Counter[str],
    home: str = "",
    away: str = "",
) -> list[SbOdds]:
    """Normalise one event's markets (MARKET_SPECS); every discarded outcome is counted.

    `home`/`away` (team names) let the team-goals markets check their desc exactly."""
    out: list[SbOdds] = []
    for m in markets:
        mid = str(m.get("id"))
        spec = MARKET_SPECS.get(mid)
        if spec is None:
            skipped["other_market"] += 1
            continue
        desc = str(m.get("desc", ""))
        if not spec.desc_ok(desc, home, away):
            skipped["unexpected_market_desc"] += 1  # an id was reused: refuse to guess
            continue
        outcomes = m.get("outcomes") or []
        if len(outcomes) != len(spec.outcomes):
            skipped["unexpected_outcome_count"] += 1
            continue
        sb_spec = str(m.get("specifier") or "")
        line = parse_specifier(mid, sb_spec)
        if line is None:
            skipped["bad_specifier"] += 1
            continue
        market = spec.code
        if not _line_in_range(market, line):
            skipped["line_out_of_range"] += len(outcomes)
            continue
        group: dict[str, SbOdds] = {}
        for o in outcomes:
            sel = spec.outcomes.get(str(o.get("id")))
            if sel is None:
                skipped["unknown_outcome"] += 1
                continue
            if not _is_active(m, o):
                skipped["inactive"] += 1
                continue
            try:
                price = float(o.get("odds"))
            except (TypeError, ValueError):
                skipped["bad_odds"] += 1
                continue
            if price <= MIN_ODDS_EXCLUSIVE:
                skipped["odds_too_low"] += 1
                continue
            # F9: hcp is already the home team's line — no sign flip for either outcome.
            group[sel] = SbOdds(market, line, sel, price, mid, sb_spec, str(o["id"]))
        full = [group[s] for s in PAIRS[market] if s in group]
        if len(full) != len(PAIRS[market]):
            skipped["missing_pair"] += len(full)
            continue
        ovr = sum(1.0 / leg.odds for leg in full) / GROUP_TOTAL.get(market, 1.0)
        if not (OVERROUND_RANGE[0] <= ovr <= OVERROUND_RANGE[1]):
            skipped["bad_overround"] += len(full)
            continue
        out += full
    return out


def parse_event(
    e: dict[str, Any], tournament_id: str, tournament_name: str, skipped: Counter[str]
) -> SbEvent | None:
    try:
        event_id = str(e["eventId"])
        home, away = str(e["homeTeamName"]), str(e["awayTeamName"])
        kickoff = datetime.fromtimestamp(int(e["estimateStartTime"]) / 1000, UTC)
    except (KeyError, TypeError, ValueError):
        skipped["malformed_event"] += 1
        return None
    if not event_id.startswith("sr:match:"):
        skipped["not_a_match"] += 1
        return None
    if e.get("status") != PREMATCH_EVENT_STATUS:
        skipped["not_prematch"] += 1
        return None
    return SbEvent(
        event_id=event_id,
        tournament_id=tournament_id,
        tournament_name=tournament_name,
        home=home,
        away=away,
        kickoff_utc=kickoff,
        odds=tuple(parse_markets(e.get("markets") or [], skipped, home, away)),
    )


def parse_pc_events(payload: dict[str, Any]) -> ParsedEvents:
    """`POST factsCenter/pcEvents` → events with their AH/OU odds (F1)."""
    data = check_envelope(payload)
    if not isinstance(data, list):
        raise SportyBetPayloadError("pcEvents data is not a list of tournaments")
    out = ParsedEvents()
    for t in data:
        tid, tname = str(t.get("id")), str(t.get("name"))
        for e in t.get("events") or []:
            ev = parse_event(e, tid, tname, out.skipped)
            if ev is not None:
                out.events.append(ev)
    return out


def parse_event_detail(payload: dict[str, Any]) -> ParsedEvents:
    """`GET factsCenter/event` → one event with all markets (F10)."""
    data = check_envelope(payload)
    out = ParsedEvents()
    sport = (data or {}).get("sport", {})
    tour = sport.get("category", {}).get("tournament", {})
    ev = parse_event(data, str(tour.get("id")), str(tour.get("name")), out.skipped)
    if ev is not None:
        out.events.append(ev)
    return out


def share_code(payload: dict[str, Any]) -> str:
    """`POST orders/share` → booking code (F11)."""
    data = check_envelope(payload)
    code = (data or {}).get("shareCode")
    if not isinstance(code, str) or not code:
        raise SportyBetPayloadError("share response has no shareCode")
    return code


SelectionKey = tuple[str, str, str, str]  # (eventId, marketId, specifier, outcomeId)


@dataclass(frozen=True)
class LiveOutcome:
    odds: float | None
    active: bool


def parse_outcomes(payload: dict[str, Any]) -> dict[SelectionKey, LiveOutcome]:
    """`POST factsCenter/Outcomes` → current odds + active flag per selection (F11)."""
    data = check_envelope(payload)
    if not isinstance(data, list):
        raise SportyBetPayloadError("Outcomes data is not a list")
    out: dict[SelectionKey, LiveOutcome] = {}
    for e in data:
        event_open = e.get("status") == PREMATCH_EVENT_STATUS
        for m in e.get("markets") or []:
            for o in m.get("outcomes") or []:
                try:
                    odds: float | None = float(o.get("odds"))
                except (TypeError, ValueError):
                    odds = None
                key = (
                    str(e.get("eventId")),
                    str(m.get("id")),
                    str(m.get("specifier") or ""),
                    str(o.get("id")),
                )
                out[key] = LiveOutcome(odds, event_open and _is_active(m, o))
    return out


def share_ticket(payload: dict[str, Any]) -> tuple[str, set[SelectionKey]]:
    """`POST orders/share` → (code, selections SportyBet says the code contains)."""
    code = share_code(payload)
    ticket = (payload.get("data") or {}).get("ticket") or {}
    echoed = {
        (
            str(x.get("eventId")),
            str(x.get("marketId")),
            str(x.get("specifier") or ""),
            str(x.get("outcomeId")),
        )
        for x in ticket.get("selections") or []
    }
    return code, echoed
