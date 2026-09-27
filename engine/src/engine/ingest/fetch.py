"""Polite HTTP fetching with a raw-response cache (CLAUDE.md rules 5 and 6).

- One request at a time, a random delay of [min_delay, max_delay] seconds before
  every request except the first, a normal desktop user agent.
- Transient failures (network errors, 5xx) are retried up to `max_retries` times.
- 401 / 403 / 429 raise `BlockedError` immediately: we back off and stop, never evade.
- Every response body is written to `data/raw/<source>/<YYYY-MM-DD>/<name>` (UTC date)
  *before* it is returned for parsing.
- A cached copy is reused when it was saved on or after `fresh_since` (a date);
  `fresh_since=None` always fetches. See `fresh_since_for_season` for the policy.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from engine.config import Settings
from engine.db.base import utcnow
from engine.logging import get_logger

log = get_logger(__name__)

_TIMEOUT_SECONDS = 30.0
_BACKOFF_MIN_SECONDS = 5.0
_BACKOFF_MAX_SECONDS = 60.0


# docs/04 §2.1 and CLAUDE.md rule 6: back off and stop on these; never retry or evade.
BLOCKED_STATUSES = frozenset({401, 403, 429})


class BlockedError(RuntimeError):
    """The site answered 401/403/429. Callers must stop requesting from that source."""


class FetchError(RuntimeError):
    """The request failed after all retries (network error, 4xx other than 403/429, 5xx)."""


@dataclass(frozen=True)
class Fetched:
    content: bytes
    path: Path
    from_cache: bool


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


class PoliteClient:
    def __init__(
        self,
        settings: Settings,
        source: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._cfg = settings.scraping
        self._root = settings.raw_path / source
        self._sleep = sleep
        self._today = today or (lambda: utcnow().date())
        self._requests_made = 0
        self._retries = 0
        self._client = httpx.Client(
            headers={"User-Agent": self._cfg.user_agent},
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @property
    def requests_made(self) -> int:
        return self._requests_made

    @property
    def retries(self) -> int:
        return self._retries

    def today(self) -> date:
        """Today's UTC date (names the cache directory for new responses)."""
        return self._today()

    # ---- cache ----
    def today_dir(self) -> Path:
        return self._root / self._today().isoformat()

    def cached(self, name: str, fresh_since: date | None) -> Path | None:
        """Most recent cached copy saved on or after `fresh_since` (None = never reuse)."""
        if fresh_since is None or not self._root.is_dir():
            return None
        # Day directories are ISO dates, so lexical order is chronological.
        for day in sorted((d.name for d in self._root.iterdir() if d.is_dir()), reverse=True):
            if day < fresh_since.isoformat():
                break
            p = self._root / day / name
            if p.is_file():
                return p
        return None

    # ---- network ----
    def get(
        self,
        url: str,
        *,
        cache_name: str,
        fresh_since: date | None,
        headers: Mapping[str, str] | None = None,
    ) -> Fetched:
        hit = self.cached(cache_name, fresh_since)
        if hit is not None:
            return Fetched(hit.read_bytes(), hit, from_cache=True)

        content = self._with_retries("GET", url, headers, None)
        return self._save(cache_name, content)

    def post_json(
        self,
        url: str,
        body: Any,
        *,
        cache_name: str,
        headers: Mapping[str, str] | None = None,
    ) -> Fetched:
        """POST a JSON body; always fetched fresh, raw response saved before parsing."""
        content = self._with_retries("POST", url, headers, body)
        return self._save(cache_name, content)

    def _save(self, cache_name: str, content: bytes) -> Fetched:
        out = self.today_dir() / cache_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
        return Fetched(content, out, from_cache=False)

    def _log_retry(self, state: RetryCallState) -> None:
        self._retries += 1
        exc = state.outcome.exception() if state.outcome else None
        log.warning(
            "http retry",
            extra={
                "fields": {
                    "url": state.args[1] if len(state.args) > 1 else None,
                    "attempt": state.attempt_number,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            },
        )

    def _with_retries(
        self, method: str, url: str, headers: Mapping[str, str] | None, body: Any
    ) -> bytes:
        retrying = Retrying(
            stop=stop_after_attempt(self._cfg.max_retries + 1),
            wait=wait_exponential(min=_BACKOFF_MIN_SECONDS, max=_BACKOFF_MAX_SECONDS),
            retry=retry_if_exception(_is_transient),
            sleep=self._sleep,
            before_sleep=self._log_retry,
            reraise=True,
        )
        try:
            return retrying(self._request_once, method, url, headers, body)
        except httpx.HTTPError as exc:
            raise FetchError(f"{method} {url} failed: {type(exc).__name__}: {exc}") from exc

    def _request_once(
        self, method: str, url: str, headers: Mapping[str, str] | None, body: Any
    ) -> bytes:
        if self._requests_made > 0:
            self._sleep(random.uniform(self._cfg.min_delay_seconds, self._cfg.max_delay_seconds))
        self._requests_made += 1
        resp = self._client.request(
            method, url, headers=dict(headers or {}), json=body if method == "POST" else None
        )
        log.info(
            "http request",
            extra={"fields": {"method": method, "url": url, "status": resp.status_code}},
        )
        if resp.status_code in BLOCKED_STATUSES:
            raise BlockedError(f"{method} {url} answered {resp.status_code}; stopping this source")
        resp.raise_for_status()
        return resp.content


def current_season_start_year(today: date) -> int:
    """European seasons start in July: 2026-09-27 -> 2026 (season 2026-27)."""
    return today.year if today.month >= 7 else today.year - 1


def season_start_year(season: str) -> int:
    return int(season[:4])


def fresh_since_for_season(season: str, today: date) -> date:
    """Oldest cache date still valid for a season's data.

    A finished season no longer changes, so any copy saved after it ended (1 July
    of its end year) is final. The current season is re-fetched once per day.
    """
    start = season_start_year(season)
    if start < current_season_start_year(today):
        return date(start + 1, 7, 1)
    return today
