"""PoliteClient: rate limiting, retries, 403/429 stop, raw cache."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from engine.config import Settings, get_settings
from engine.ingest.fetch import (
    BlockedError,
    FetchError,
    PoliteClient,
    current_season_start_year,
    fresh_since_for_season,
)

TODAY = date(2026, 9, 27)


def _settings(tmp_path: Path) -> Settings:
    base = get_settings()
    return base.model_copy(
        update={"general": base.general.model_copy(update={"raw_dir": str(tmp_path / "raw")})}
    )


def _client(
    tmp_path: Path, handler: object, sleeps: list[float], today: date = TODAY
) -> PoliteClient:
    assert callable(handler)
    return PoliteClient(
        _settings(tmp_path),
        "src",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        today=lambda: today,
    )


def test_saves_raw_before_returning_and_delays_between_requests(tmp_path: Path) -> None:
    sleeps: list[float] = []
    with _client(
        tmp_path, lambda r: httpx.Response(200, content=b"x" + r.url.path.encode()), sleeps
    ) as c:
        a = c.get("https://ex.test/a", cache_name="a.csv", fresh_since=None)
        b = c.get("https://ex.test/b", cache_name="b.csv", fresh_since=None)
    assert (tmp_path / "raw" / "src" / "2026-09-27" / "a.csv").read_bytes() == b"x/a"
    assert a.content == b"x/a" and not a.from_cache and b.content == b"x/b"
    cfg = get_settings().scraping
    assert len(sleeps) == 1  # none before the first request
    assert cfg.min_delay_seconds <= sleeps[0] <= cfg.max_delay_seconds


@pytest.mark.parametrize("status", [401, 403, 429])
def test_blocked_is_not_retried(tmp_path: Path, status: int) -> None:
    calls: list[int] = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status)

    with _client(tmp_path, handler, []) as c, pytest.raises(BlockedError):
        c.get("https://ex.test/a", cache_name="a", fresh_since=None)
    assert len(calls) == 1
    assert not (tmp_path / "raw" / "src" / "2026-09-27" / "a").exists()


def test_transient_errors_are_retried_then_succeed(tmp_path: Path) -> None:
    responses = [httpx.Response(503), httpx.Response(502), httpx.Response(200, content=b"ok")]
    with _client(tmp_path, lambda r: responses.pop(0), []) as c:
        assert c.get("https://ex.test/a", cache_name="a", fresh_since=None).content == b"ok"
        assert (c.requests_made, c.retries) == (3, 2)


def test_network_errors_are_retried_and_counted(tmp_path: Path) -> None:
    attempts: list[int] = []

    def handler(r: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("slow", request=r)
        return httpx.Response(200, content=b"ok")

    with _client(tmp_path, handler, []) as c:
        assert c.get("https://ex.test/a", cache_name="a", fresh_since=None).content == b"ok"
        assert c.retries == 1


def test_retries_exhausted_raise_fetch_error(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500)

    with _client(tmp_path, handler, []) as c, pytest.raises(FetchError):
        c.get("https://ex.test/a", cache_name="a", fresh_since=None)
    assert len(calls) == get_settings().scraping.max_retries + 1


def test_404_is_not_retried(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    with _client(tmp_path, handler, []) as c, pytest.raises(FetchError):
        c.get("https://ex.test/a", cache_name="a", fresh_since=None)
    assert len(calls) == 1


def test_cache_respects_fresh_since(tmp_path: Path) -> None:
    root = tmp_path / "raw" / "src"
    for day, body in (("2026-05-01", b"old"), ("2026-07-02", b"final")):
        (root / day).mkdir(parents=True)
        (root / day / "f").write_bytes(body)

    def boom(r: httpx.Request) -> httpx.Response:
        raise AssertionError("should have used the cache")

    with _client(tmp_path, boom, []) as c:
        got = c.get("u", cache_name="f", fresh_since=date(2026, 7, 1))
        assert (got.content, got.from_cache) == (b"final", True)
    with _client(tmp_path, lambda r: httpx.Response(200, content=b"new"), []) as c:
        # today's policy: copies from earlier days are stale
        assert c.get("https://ex.test/f", cache_name="f", fresh_since=TODAY).content == b"new"


@pytest.mark.parametrize(
    ("today", "season", "expected"),
    [
        (date(2026, 9, 27), "2025-26", date(2026, 7, 1)),  # finished: any copy after it ended
        (date(2026, 9, 27), "2019-20", date(2020, 7, 1)),
        (date(2026, 9, 27), "2026-27", date(2026, 9, 27)),  # current: today only
        (date(2026, 3, 1), "2025-26", date(2026, 3, 1)),
    ],
)
def test_fresh_since_for_season(today: date, season: str, expected: date) -> None:
    assert fresh_since_for_season(season, today) == expected


@pytest.mark.parametrize(
    ("today", "year"),
    [(date(2026, 7, 1), 2026), (date(2026, 6, 30), 2025), (date(2027, 1, 5), 2026)],
)
def test_current_season_start_year(today: date, year: int) -> None:
    assert current_season_start_year(today) == year


def test_post_json_sends_body_and_saves_raw(tmp_path: Path) -> None:
    seen: list[tuple[str, bytes]] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append((r.method, r.content))
        return httpx.Response(200, content=b'{"ok":1}')

    with _client(tmp_path, handler, []) as c:
        got = c.post_json("https://ex.test/p", [{"a": 1}], cache_name="p.json")
    import json

    assert [(m, json.loads(b)) for m, b in seen] == [("POST", [{"a": 1}])]
    assert got.path.read_bytes() == b'{"ok":1}' and not got.from_cache
