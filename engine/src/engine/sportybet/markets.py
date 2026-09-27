"""SportyBet payload parsing and normalisation (docs/04 §2.3). Pure.

Every id and convention here is recorded in
docs/discovered/sportybet/2026-09-27/endpoints.md (facts F3–F9):
- market "16" = full-time two-way Asian Handicap, specifier `hcp=<x>` is the HOME line
  (F9: "Home (-0.5)" / "Away (+0.5)" for hcp=-0.5), outcomes 1714 home / 1715 away;
- market "18" = full-time Over/Under, specifier `total=<x>`, outcomes 12 over / 13 under;
- market "14" (3-way European handicap), 66/68 (1st half), 19/20 (team totals) are never used;
- kickoff `estimateStartTime` is epoch milliseconds (UTC instant);
- active = market `status == 0`, outcome `isActive == 1`, not `banned`.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

OK_BIZ_CODE = 10000
AH_MARKET_ID = "16"
OU_MARKET_ID = "18"
AH_DESC_PREFIX = "Asian Handicap"
OU_DESC = "Over/Under"
OUTCOMES: dict[str, dict[str, str]] = {
    AH_MARKET_ID: {"1714": "home", "1715": "away"},
    OU_MARKET_ID: {"12": "over", "13": "under"},
}
SPECIFIER_KEY = {AH_MARKET_ID: "hcp", OU_MARKET_ID: "total"}
MARKET_CODE = {AH_MARKET_ID: "AH", OU_MARKET_ID: "OU"}
PAIRS = {"AH": ("home", "away"), "OU": ("over", "under")}
PREMATCH_EVENT_STATUS = 0

# docs/04 §2.3 discard bounds.
MIN_ODDS_EXCLUSIVE = 1.01
AH_LINE_RANGE = (-3.5, 3.5)
OU_LINE_RANGE = (0.5, 5.5)
OVERROUND_RANGE = (1.00, 1.15)


class SportyBetPayloadError(ValueError):
    """The response is not the shape discovered (or bizCode signals an error)."""


@dataclass(frozen=True)
class SbOdds:
    market: str  # AH | OU
    line: float  # AH: home perspective
    selection: str  # home | away | over | under
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
    key = SPECIFIER_KEY[market_id]
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
    lo, hi = AH_LINE_RANGE if market == "AH" else OU_LINE_RANGE
    return lo <= line <= hi


def parse_markets(markets: list[dict[str, Any]], skipped: Counter[str]) -> list[SbOdds]:
    """Normalise one event's AH/OU markets; every discarded outcome is counted by reason."""
    out: list[SbOdds] = []
    for m in markets:
        mid = str(m.get("id"))
        if mid not in OUTCOMES:
            skipped["other_market"] += 1
            continue
        desc = str(m.get("desc", ""))
        if (mid == AH_MARKET_ID and not desc.startswith(AH_DESC_PREFIX)) or (
            mid == OU_MARKET_ID and desc != OU_DESC
        ):
            skipped["unexpected_market_desc"] += 1  # an id was reused: refuse to guess
            continue
        outcomes = m.get("outcomes") or []
        if len(outcomes) != 2:
            skipped["not_two_way"] += 1
            continue
        spec = str(m.get("specifier") or "")
        line = parse_specifier(mid, spec)
        if line is None:
            skipped["bad_specifier"] += 1
            continue
        market = MARKET_CODE[mid]
        if not _line_in_range(market, line):
            skipped["line_out_of_range"] += len(outcomes)
            continue
        pair: dict[str, SbOdds] = {}
        for o in outcomes:
            sel = OUTCOMES[mid].get(str(o.get("id")))
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
            pair[sel] = SbOdds(market, line, sel, price, mid, spec, str(o["id"]))
        a, b = (pair.get(s) for s in PAIRS[market])
        if a is None or b is None:
            skipped["missing_pair"] += len(pair)
            continue
        ovr = 1.0 / a.odds + 1.0 / b.odds
        if not (OVERROUND_RANGE[0] <= ovr <= OVERROUND_RANGE[1]):
            skipped["bad_overround"] += 2
            continue
        out += [a, b]
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
        odds=tuple(parse_markets(e.get("markets") or [], skipped)),
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
