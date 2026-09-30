# 04 — SportyBet Integration (fixtures, odds, booking codes, results)

SportyBet has no public API. The web app at `https://www.sportybet.com/ng/`
loads its data from internal JSON endpoints. Strategy:

1. **Discover** those endpoints once with a real browser and record them.
2. **Read** fixtures/odds by calling the discovered JSON endpoints with
   `httpx` (fast, stable) — falling back to Playwright-in-page fetches if
   plain HTTP is rejected.
3. **Book** slips by driving the real betslip UI with Playwright (the most
   robust path, since it is exactly what a human does), unless discovery
   finds a booking endpoint that works without login — then prefer it, with
   the UI flow kept as fallback.

This is automation of a site whose terms may not permit it. Keep volume
tiny (a handful of page loads per run), never log in, never place bets,
and stop on 403/429 or any captcha. **Never attempt to solve or bypass a
captcha or bot check.** If one appears, fail the job with a clear message
and fall back to showing the selections for manual entry.

---

## 1. Discovery (`make discover`, Phase 3 task 1)

Implement `sportybet/discovery.py` that launches **headed** Chromium,
navigates to the football section for each enabled league, and records every
XHR/fetch response (`page.on("response")`) whose content type is JSON.

It must save, under `docs/discovered/sportybet/<date>/`:
- `endpoints.md` — for each relevant endpoint: method, URL template, query
  params, required headers/cookies, and what it returns;
- trimmed sample payloads (`*.json`, max ~200 lines each).

Then, **with the user watching**, manually add 2 selections to the betslip
and click the booking button, recording the request made and the booking
code returned.

### 1.1 Facts discovery must establish (fill all of these in `endpoints.md`)
| # | fact | why |
|---|---|---|
| F1 | endpoint(s) listing upcoming football events by tournament, with pagination | fixtures |
| F2 | how a tournament/league is identified (IDs for our 5 leagues) | filtering |
| F3 | event identifier format (expected like `sr:match:<n>`) | `matches.sportybet_event_id` |
| F4 | team names and kickoff time fields + timezone of the timestamp | mapping, kickoff |
| F5 | market id + name + specifier for **two-way Asian Handicap** (full time) | AH odds |
| F6 | market id + name + specifier for **Over/Under total goals** (full time) | OU odds |
| F7 | outcome ids/names within those markets (home/away, over/under) | selections |
| F8 | how market/outcome active/suspended status is represented | skip suspended |
| F9 | sign convention of the handicap specifier (is `hcp=-0.5` home −0.5?) | correctness |
| F10 | how to request **all lines** for a market (main line vs alternative lines) | line choice |
| F11 | booking flow: request, payload, response field holding the code | booking |
| F12 | results/scores endpoint for finished events (or the results page) | settlement |

Hypotheses to **verify, not assume**: SportyBet uses Sportradar-style ids,
where full-time Total is often market `18` with specifier `total=X` and a
two-way Handicap is often market `16` with specifier `hcp=X`, while market
`14` is a **three-way European handicap** (with a draw outcome) — which is
**not** Asian and must be excluded. Confirm by checking the market name and
that it has exactly two outcomes. Half-time markets must be excluded.

### 1.2 F9 check (mandatory)
Pick an event where one team is a clear favourite. Confirm in the payload
and on the rendered page which team is "−" in the AH market. Record the
event, screenshot path and conclusion. The adapter converts everything to
**home-perspective lines**; a unit test uses the saved sample to lock the
convention.

---

## 2. Odds client (`sportybet/client.py`, `sportybet/markets.py`)

### 2.1 Behaviour
- Uses the discovered endpoints with `httpx`, the configured user agent,
  randomised delays between requests (`min_delay_seconds..max_delay_seconds`),
  `tenacity` retry on network errors/5xx only (max `max_retries`),
  immediate stop on 401/403/429.
- If plain `httpx` requests are rejected but the page works in a browser,
  switch to Playwright: open the site once, then call the endpoints with
  `page.evaluate("fetch(...)")` inside the page context. Implement both
  transports behind one interface `SportyBetTransport`.
- Save every raw response under `data/raw/sportybet/<date>/`.

### 2.2 Fetch scope
- `make odds` fetches events with kickoff in the next 72 hours (covers the
  weekend when run on Friday) for enabled leagues only. **Changed 2026-09-30:**
  the window is `general.horizon_hours` (7 days) — see docs/05 §8.6.
- For each event: all full-time two-way AH lines and all full-time O/U lines
  available, both outcomes, with active status.

### 2.3 Normalisation into `odds_snapshots`
- `market ∈ {AH, OU}`; `line` float, AH in home perspective (per F9);
  `selection ∈ {home, away, over, under}`; odds decimal.
- Keep raw `sb_market_id`, `sb_specifier`, `sb_outcome_id` — the booking
  step needs them.
- Discard (with counted reasons): suspended outcomes, odds ≤ 1.01, markets
  that are not two-way, half-time/period markets, lines outside
  [−3.5, +3.5] for AH or [0.5, 5.5] for OU.
- Resolve team names through `team_aliases` (source `sportybet`). Upsert the
  event into `matches` (status `scheduled`) keyed on
  `(league, season, home, away)`, setting `sportybet_event_id`. If
  unresolved, write `unresolved_names` and skip the event.
- Sanity: a two-way market's overround `1/o1 + 1/o2` must be in
  [1.00, 1.15]; otherwise discard that line and log (catches parsing mix-ups).

### 2.4 Tests
Parser tests run against the saved sample payloads from discovery (no
network in tests). Include one test per fact F3–F9.

---

## 3. Booking codes (`sportybet/booking.py`)

### 3.1 Input
A slip's legs, each carrying `sportybet_event_id`, `sb_market_id`,
`sb_specifier`, `sb_outcome_id`, and the odds at pick time.

### 3.2 Preferred path (only if discovery F11 found it working without login)
Call the booking endpoint with the selections. Validate the response
contains a non-empty code. Save raw request/response.

### 3.3 UI path (default / fallback) with Playwright
1. Open the event page for leg 1; wait for markets to render.
2. Locate the market and outcome using **stable attributes discovered in
   Phase 3** (data attributes or ids), never visible text alone and never
   absolute XPaths. Put every selector in one file `sportybet/selectors.py`.
3. Click the outcome; verify the betslip count increased by one and the
   betslip shows the expected odds.
4. Repeat for every leg.
5. Verify betslip leg count == slip leg count.
6. Click the book/share control; read the code from the result dialog.
7. Save a screenshot of the betslip and dialog to
   `data/raw/sportybet/booking/<slip_id>/`.
8. Clear the betslip at the end.

### 3.4 Odds drift rule
If any leg's current odds differ from pick-time odds by more than 3% or the
market is suspended: do **not** book. Set `booking_status = failed`,
`booking_error = "odds moved: <leg details>"`, and let the user re-run
`make picks` (which recomputes edges) then `make book`.

### 3.5 Failure handling
On any failure: screenshot, `booking_status = failed`, error message saved,
job continues to the next slip. The dashboard always shows the full leg
list (match, market, line, selection, odds) so the user can enter it
manually and mark `booking_status = manual` with a pasted code.

### 3.6 SportyBet selection limit
Discovery must record the maximum number of selections per betslip. If a
slip exceeds it, the slip builder (docs/05) must cap below it.

---

## 4. Results for settlement (`sportybet/results.py`)

Primary: SportyBet results endpoint from F12, matched by
`sportybet_event_id`. Secondary: football-data.co.uk current-season CSV
(re-ingest), matched by resolved teams + season. If both exist and disagree,
mark the match `needs_review`, log it, and do not settle it; show it on the
Data health page. Full-time result only (90 minutes + stoppage time; extra
time never counts).

Postponed/cancelled/abandoned: legs become `void` (multiplier 1).
