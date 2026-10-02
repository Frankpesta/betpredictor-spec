"""SportyBet results for settlement (docs/04 §4; facts: endpoints.md F12).

`GET factsCenter/eventResultList` for one tournament and one Lagos calendar day.
The **90-minute score is gameScore[0] + gameScore[1]** (first + second half):
`regularTimeScore` turned out to be the first-half score and `setScore` includes extra
time and penalties (discovered 2026-09-27). Results without per-half scores cannot be
used. Statuses other than the observed "Ended" (3 / 4) and "AP" (after penalties, 4;
accepted by the user 2026-10-02) are reported, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from engine.sportybet.markets import check_envelope

RESULTS_PATH = "/factsCenter/eventResultList"
PAGE_SIZE = 100
ENDED_STATUSES = frozenset({3, 4})  # both observed with matchStatus "Ended"
# "AP" = after penalties (docs/discovered/sportybet/2026-10-02/results-status-AP.md)
ENDED_MATCH_STATUSES = frozenset({"Ended", "AP"})


@dataclass(frozen=True)
class SbResult:
    event_id: str
    status: int | None
    match_status: str
    score_90: tuple[int, int] | None  # None when per-half scores are missing/inconsistent

    @property
    def ended(self) -> bool:
        return self.status in ENDED_STATUSES and self.match_status in ENDED_MATCH_STATUSES


def _pair(s: str) -> tuple[int, int]:
    h, a = s.split(":")
    return int(h), int(a)


def ninety_minute_score(event: dict[str, Any]) -> tuple[int, int] | None:
    periods = event.get("gameScore")
    if not isinstance(periods, list) or len(periods) < 2:
        return None
    try:
        h1, h2 = _pair(periods[0]), _pair(periods[1])
    except (ValueError, AttributeError):
        return None
    score = (h1[0] + h2[0], h1[1] + h2[1])
    if len(periods) == 2 and isinstance(event.get("setScore"), str):
        try:
            if _pair(event["setScore"]) != score:
                return None  # halves do not add up to the final score: refuse to guess
        except ValueError:
            return None
    return score


def parse_results(payload: dict[str, Any]) -> tuple[dict[str, SbResult], int]:
    """→ ({eventId: result}, totalNum)."""
    data = check_envelope(payload) or {}
    out: dict[str, SbResult] = {}
    for t in data.get("tournaments") or []:
        for e in t.get("events") or []:
            eid = str(e.get("eventId"))
            status = e.get("status")
            out[eid] = SbResult(
                event_id=eid,
                status=int(status) if isinstance(status, int) else None,
                match_status=str(e.get("matchStatus") or ""),
                score_90=ninety_minute_score(e),
            )
    return out, int(data.get("totalNum") or 0)


def day_window_ms(day: date, tz: ZoneInfo) -> tuple[int, int]:
    """[00:00, 23:59:59.999] of a local calendar day, as epoch milliseconds."""
    start = datetime.combine(day, time(0, 0), tzinfo=tz)
    end = start + timedelta(days=1) - timedelta(milliseconds=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)
