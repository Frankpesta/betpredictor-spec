"""Headed-browser SportyBet endpoint/market discovery (docs/04 §1).

`make discover` opens a *visible* Chromium window on SportyBet Nigeria and records
every JSON response while the user browses. It never logs in, never places a bet,
and never touches a captcha: if one appears the user closes the window.

Recorded under data/raw/sportybet/<UTC date>/discovery/:
  index.jsonl        one line per JSON response: seq, method, url, status, request
                     headers (cookie *values* redacted), post data, body file
  NNNN_<slug>.json   response bodies
  shots/NNNN.png     a screenshot after every main-frame navigation
The session ends when the browser window is closed. The facts F1-F12 are then
written up by hand in docs/discovered/sportybet/<date>/endpoints.md.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from engine.db.base import utcnow
from engine.logging import get_logger

if TYPE_CHECKING:
    from playwright.sync_api import Frame, Page, Response

    from engine.jobs import JobContext

log = get_logger(__name__)

START_URL = "https://www.sportybet.com/{country}/"
_REDACT = re.compile(r"cookie|authorization|token", re.IGNORECASE)
_MAX_SLUG = 60
_POLL_MS = 500
_RENDER_WAIT_MS = 20_000
_SETTLE_MS = 2_000

INSTRUCTIONS = """
SportyBet discovery session — a Chromium window is open. Please, in that window:
  1. Open Football and visit each league: Premier League, LaLiga, Serie A,
     Bundesliga, Ligue 1 (upcoming matches list).
  2. Visit international fixtures (e.g. World Cup qualifiers / friendlies).
  3. Open ONE match page with a clear favourite and view its Asian Handicap and
     Over/Under markets (including 'all lines' if there is such a toggle).
  4. Add 2 selections from different matches to the betslip and click the
     booking / "Book Bet" button (do NOT log in, do NOT place a bet).
  5. Open the Results page for football, if you can find it.
  6. Close the browser window to finish.
If a captcha or bot check appears: do not solve it — just close the window.
"""


def _slug(url: str) -> str:
    parts = urlsplit(url)
    s = re.sub(r"[^A-Za-z0-9]+", "_", parts.path).strip("_")
    return s[-_MAX_SLUG:] or "root"


def _redacted(headers: dict[str, str]) -> dict[str, str]:
    return {k: ("<redacted>" if _REDACT.search(k) else v) for k, v in headers.items()}


class Recorder:
    def __init__(self, out_dir: Path) -> None:
        self.out = out_dir
        (self.out / "shots").mkdir(parents=True, exist_ok=True)
        self.index = (self.out / "index.jsonl").open("a", encoding="utf-8")
        self.seq = 0
        self.shots = 0
        self.errors = 0
        self.pending_shots: list[str] = []

    def on_response(self, resp: Response) -> None:
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype:
            return
        self.seq += 1
        name = f"{self.seq:04d}_{_slug(resp.url)}.json"
        req = resp.request
        entry: dict[str, Any] = {
            "seq": self.seq,
            "ts": utcnow().isoformat(),
            "method": req.method,
            "url": resp.url,
            "status": resp.status,
            "request_headers": _redacted(req.headers),
            "post_data": req.post_data,
            "body_file": name,
        }
        try:
            (self.out / name).write_bytes(resp.body())
        except Exception as exc:  # noqa: BLE001 - body can vanish on navigation; record why
            self.errors += 1
            entry["body_error"] = f"{type(exc).__name__}: {exc}"
        self.index.write(json.dumps(entry) + "\n")
        self.index.flush()
        if resp.status in (401, 403, 429):
            log.warning("sportybet answered %s for %s", resp.status, resp.url)

    def on_navigated(self, page: Page, frame: Frame) -> None:
        # Only queue here; screenshots are taken from the main loop (sync API rule).
        if frame == page.main_frame:
            self.pending_shots.append(frame.url)

    def take_pending_shots(self, page: Page) -> None:
        while self.pending_shots and not page.is_closed():
            url = self.pending_shots.pop(0)
            self.shots += 1
            path = self.out / "shots" / f"{self.shots:04d}.png"
            try:
                # Wait for the SPA to render its markets, not just the HTML shell.
                page.wait_for_load_state("networkidle", timeout=_RENDER_WAIT_MS)
                page.wait_for_timeout(_SETTLE_MS)
                page.screenshot(path=str(path), full_page=True)
                log.info("navigated", extra={"fields": {"url": url, "shot": path.name}})
            except Exception as exc:  # noqa: BLE001 - a failed screenshot must not end the session
                self.errors += 1
                log.warning("screenshot failed: %s", exc)

    def close(self) -> None:
        self.index.close()


def run_discovery(ctx: JobContext) -> str:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    s = ctx.settings
    out = s.raw_path / "sportybet" / utcnow().date().isoformat() / "discovery"
    rec = Recorder(out)
    print(INSTRUCTIONS, flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=s.scraping.user_agent,
            locale="en-NG",
            timezone_id=s.general.timezone,
            viewport={"width": 1400, "height": 900},
        )
        page = context.new_page()
        page.on("response", rec.on_response)
        page.on("framenavigated", lambda frame: rec.on_navigated(page, frame))
        context.on("page", lambda p: p.on("response", rec.on_response))
        page.goto(START_URL.format(country=s.scraping.sportybet_country))
        # Poll through Playwright (not time.sleep) so events keep being delivered;
        # finish when the user has closed every window.
        while browser.is_connected():
            open_pages = [p for p in context.pages if not p.is_closed()]
            if not open_pages:
                break
            try:
                if not page.is_closed():
                    rec.take_pending_shots(page)
                open_pages[0].wait_for_timeout(_POLL_MS)
            except PlaywrightError:
                # Closing the window mid-wait raises; that is the normal end of a session.
                if browser.is_connected() and any(not p.is_closed() for p in context.pages):
                    raise
                break
        rec.close()
        if browser.is_connected():
            browser.close()
    ctx.note("json_responses", rec.seq)
    ctx.note("screenshots", rec.shots)
    ctx.note("record_errors", rec.errors)
    ctx.note("out_dir", str(out))
    return f"recorded {rec.seq} JSON responses and {rec.shots} screenshots in {out}"
