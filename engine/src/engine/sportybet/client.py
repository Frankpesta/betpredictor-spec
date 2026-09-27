"""SportyBet read/booking client (docs/04 §2.1, §3.2).

Endpoints are documented in docs/discovered/sportybet/2026-09-27/endpoints.md.
`SportyBetTransport` has two implementations behind one interface:
- `HttpxTransport` (default; verified working with plain HTTP on 2026-09-27),
- `PlaywrightTransport` (fallback: open the site once, then `fetch()` inside the page).
Both save every raw response under data/raw/sportybet/<date>/ before parsing, respect
the configured delays, and stop on 401/403/429 or a non-JSON (bot-check) response.
Never logs in, never places a bet.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from typing import Any, Protocol

from engine.config import Settings
from engine.db.base import utcnow
from engine.ingest.fetch import BLOCKED_STATUSES, BlockedError, PoliteClient
from engine.logging import get_logger
from engine.sportybet import markets as mk

log = get_logger(__name__)

SOURCE = "sportybet"
BASE = "https://www.sportybet.com"
API = BASE + "/api/{country}"
PC_EVENTS = "/factsCenter/pcEvents"
EVENT = "/factsCenter/event"
OUTCOMES = "/factsCenter/Outcomes"
SHARE = "/orders/share"
SPORT_FOOTBALL = "sr:sport:1"
ODDS_MARKET_IDS = f"{mk.AH_MARKET_ID},{mk.OU_MARKET_ID}"
PRODUCT_ID_PREMATCH = "3"


def site_headers(settings: Settings) -> dict[str, str]:
    """Headers the site itself sends (F-transport); no cookies, no auth."""
    country = settings.scraping.sportybet_country
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en",
        "clientid": "web",
        "operid": "2",
        "platform": "web",
        "referer": f"{BASE}/{country}/sport/football",
    }


class SportyBetTransport(Protocol):
    def get_json(self, path: str, params: dict[str, str], cache_name: str) -> dict[str, Any]: ...
    def post_json(self, path: str, body: Any, cache_name: str) -> dict[str, Any]: ...
    def close(self) -> None: ...


class HttpxTransport:
    def __init__(self, settings: Settings, client: PoliteClient | None = None) -> None:
        self._api = API.format(country=settings.scraping.sportybet_country)
        self._headers = site_headers(settings)
        self._client = client or PoliteClient(settings, SOURCE)

    @property
    def requests_made(self) -> int:
        return self._client.requests_made

    def get_json(self, path: str, params: dict[str, str], cache_name: str) -> dict[str, Any]:
        from urllib.parse import urlencode

        url = f"{self._api}{path}?{urlencode(params)}"
        got = self._client.get(url, cache_name=cache_name, fresh_since=None, headers=self._headers)
        return mk.load_json(got.content)

    def post_json(self, path: str, body: Any, cache_name: str) -> dict[str, Any]:
        got = self._client.post_json(
            f"{self._api}{path}", body, cache_name=cache_name, headers=self._headers
        )
        return mk.load_json(got.content)

    def close(self) -> None:
        self._client.close()


class PlaywrightTransport:
    """Fallback: one real page on the site, then same-origin fetch() calls inside it."""

    _FETCH_JS = """async ([url, method, body, headers]) => {
        const r = await fetch(url, {method, headers, body: body === null ? undefined : body,
                                    credentials: 'include'});
        return {status: r.status, text: await r.text()};
    }"""

    def __init__(self, settings: Settings, sleep: Callable[[float], None] = time.sleep) -> None:
        from playwright.sync_api import sync_playwright

        self._s = settings
        self._sleep = sleep
        self._requests = 0
        self._api = API.format(country=settings.scraping.sportybet_country)
        self._headers = {**site_headers(settings), "content-type": "application/json;charset=UTF-8"}
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=settings.scraping.headless)
        ctx = self._browser.new_context(
            user_agent=settings.scraping.user_agent, timezone_id=settings.general.timezone
        )
        self._page = ctx.new_page()
        self._page.goto(f"{BASE}/{settings.scraping.sportybet_country}/sport/football")

    @property
    def requests_made(self) -> int:
        return self._requests

    def _call(self, method: str, url: str, body: Any, cache_name: str) -> dict[str, Any]:
        if self._requests:
            cfg = self._s.scraping
            self._sleep(random.uniform(cfg.min_delay_seconds, cfg.max_delay_seconds))
        self._requests += 1
        payload = None if body is None else json.dumps(body)
        res = self._page.evaluate(self._FETCH_JS, [url, method, payload, self._headers])
        raw = str(res["text"]).encode("utf-8")
        out = self._s.raw_path / SOURCE / utcnow().date().isoformat() / cache_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
        if int(res["status"]) in BLOCKED_STATUSES:
            raise BlockedError(f"{method} {url} answered {res['status']} (in-page)")
        return mk.load_json(raw)

    def get_json(self, path: str, params: dict[str, str], cache_name: str) -> dict[str, Any]:
        from urllib.parse import urlencode

        return self._call("GET", f"{self._api}{path}?{urlencode(params)}", None, cache_name)

    def post_json(self, path: str, body: Any, cache_name: str) -> dict[str, Any]:
        return self._call("POST", f"{self._api}{path}", body, cache_name)

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()


class SportyBetClient:
    def __init__(self, transport: SportyBetTransport) -> None:
        self.t = transport

    def upcoming_events(self, label: str, tournament_ids: tuple[str, ...]) -> mk.ParsedEvents:
        """F1: all events (with all AH/OU lines) for a competition's tournaments."""
        body = [
            {
                "sportId": SPORT_FOOTBALL,
                "marketId": ODDS_MARKET_IDS,
                "tournamentId": [list(tournament_ids)],
            }
        ]
        stamp = utcnow().strftime("%H%M%S")
        payload = self.t.post_json(PC_EVENTS, body, f"pcEvents_{label}_{stamp}.json")
        return mk.parse_pc_events(payload)

    def event_detail(self, event_id: str) -> mk.ParsedEvents:
        """F10: one event with every market (used for spot checks and drift re-checks)."""
        slug = event_id.replace(":", "_")
        payload = self.t.get_json(
            EVENT,
            {"eventId": event_id, "productId": PRODUCT_ID_PREMATCH},
            f"event_{slug}_{utcnow():%H%M%S}.json",
        )
        return mk.parse_event_detail(payload)

    def book(self, selections: list[dict[str, str]], label: str) -> str:
        """F11: booking code for selections {eventId, marketId, specifier, outcomeId}."""
        payload = self.t.post_json(SHARE, {"selections": selections}, f"share_{label}.json")
        return mk.share_code(payload)


def make_transport(settings: Settings, kind: str = "httpx") -> SportyBetTransport:
    if kind == "httpx":
        return HttpxTransport(settings)
    if kind == "playwright":
        return PlaywrightTransport(settings)
    raise ValueError(f"unknown transport {kind!r}")
