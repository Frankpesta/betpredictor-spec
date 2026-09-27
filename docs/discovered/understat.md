# Understat — discovered facts

Verified: **2026-09-27** (Phase 1 discovery). Raw copies:
`data/raw/understat/2026-09-27/{league_EPL_2025.html, league.min.js, getLeagueData_EPL_2025.json}`.

## How match data is delivered
- The league page `https://understat.com/league/EPL/2025` **no longer embeds**
  `JSON.parse('...')` data (0 occurrences in the HTML).
- The page's `js/league.min.js` loads the data via XHR:
  ```js
  $.ajax({url:"getLeagueData/"+league+"/"+season, type:"get", dataType:"json", ...
    success: function(data){ datesData=data.dates, teamsData=data.teams, ... }
  ```
- Endpoint: **`GET https://understat.com/getLeagueData/{league}/{season}`**
  - `league` = Understat key (`EPL`, `La_liga`, `Serie_A`, `Bundesliga`, `Ligue_1`)
  - `season` = **start year** of the season (`2025` = 2025-26).
  - Sent with `X-Requested-With: XMLHttpRequest` and a `Referer` of the league
    page (as the site's own JS does). Response: `200`, `text/javascript;charset=UTF-8`,
    gzip-encoded JSON (httpx decodes automatically).
- Response keys: `teams` (dict, 20), `players` (list), `dates` (list of matches, 380 for EPL 2025).

## Option 1 (PyPI `understat`) — tested, not used
`understat==0.1.14` works against the live site (returns 380 EPL 2025 results; it
calls the same `getLeagueData` endpoint). Not adopted because:
- its runtime requirements pin `aiohttp==3.8.3` (fails to build on this machine) and
  `pytest==7.2.0` (conflicts with our pytest ≥ 8);
- it gives no hook to cache the raw response (CLAUDE.md rule 5) or to apply our
  rate limit / 403-429 stop (rule 6).
So we use option 2: our own `httpx` adapter against the same endpoint.

## Match object (`dates[]`) — fields used
| field | example | notes |
|---|---|---|
| `id` | `"28778"` | string |
| `isResult` | `true` | bool; false for unplayed fixtures |
| `h.title` / `a.title` | `"Liverpool"` / `"Bournemouth"` | team names (Understat spelling) |
| `goals.h` / `goals.a` | `"4"` / `"2"` | strings; null when not played |
| `xG.h` / `xG.a` | `"2.33007"` / `"1.57303"` | strings; null when not played |
| `datetime` | `"2025-08-15 19:00:00"` | **UTC** (see below) |

`forecast`, `h.id`, `h.short_title` are present but unused.

## Timezone of `datetime` = UTC (mostly — not reliable, never used for kickoffs)
- Liverpool v Bournemouth, 15 Aug 2025, kicked off 20:00 BST → Understat `19:00:00`.
- Aston Villa v Newcastle, 16 Aug 2025, kicked off 12:30 BST → Understat `11:30:00`.
- **But** for Aug–Oct 2019 and on spring DST-change Sundays (2024-03-31, 2025-03-30)
  Understat is one hour late: Liverpool v Norwich 2019-08-09 (20:00 BST = 19:00 UTC)
  is stored as `2019-08-09 20:00:00`. 95.2% of all joined matches agree with
  football-data to the minute. We therefore take kickoff times only from
  football-data and use Understat's `datetime` only for the diagnostic comparison.

## Team spellings seen (EPL 2025)
Arsenal, Aston Villa, Bournemouth, Brentford, Brighton, Burnley, Chelsea, Crystal Palace,
Everton, Fulham, Leeds, Liverpool, **Manchester City, Manchester United, Newcastle United,
Nottingham Forest, Wolverhampton Wanderers**, Sunderland, Tottenham, West Ham.
(Bold = differs from football-data.) Full per-league alias list lives in
`engine/src/engine/mapping/aliases_seed.toml`.

## Sample (trimmed)
```json
{"dates": [
  {"id": "28778", "isResult": true,
   "h": {"id": "87", "title": "Liverpool", "short_title": "LIV"},
   "a": {"id": "73", "title": "Bournemouth", "short_title": "BOU"},
   "goals": {"h": "4", "a": "2"}, "xG": {"h": "2.33007", "a": "1.57303"},
   "datetime": "2025-08-15 19:00:00",
   "forecast": {"w": "0.5498", "d": "0.2276", "l": "0.2226"}}
]}
```

## Join results from the full ingest (2026-09-27)
- 40 league-season files (5 leagues × 2019..2026), all HTTP 200.
- 149 Understat names ↔ 149 football-data teams, one-to-one per league
  (`aliases_seed.toml`). Fuzzy proposals got 3 of 149 **wrong** and were corrected
  by review: West Bromwich Albion (proposed West Ham), Wolverhampton Wanderers
  (proposed Southampton), Paris Saint Germain (proposed Paris FC).
- xG joined for 12,708 of 12,709 finished football-data matches; 100% for 39 of 40
  league-seasons.
- **Ligue 1 2026-27: 44/45 (97.8%).** Understat lists *Paris Saint Germain v Rennes*
  on 2026-08-23; football-data lists *Rennes v Paris SG* (2-2). ESPN / Sky Sports
  report the match at Roazhon Park (Rennes at home), so **Understat has home/away
  swapped** for this fixture. It is not joined (the join key is home/away).
- **1 goal mismatch:** Union Berlin v Bochum 2024-12-14 — football-data 0-2
  (result awarded after a lighter hit the Bochum keeper), Understat 1-1 (as played).
  football-data's official result is kept; xG is stored.

**Decision (user, 2026-09-27):** the Ligue 1 2026-27 swapped fixture is accepted as a
known Understat source error; no override. That match uses the goals-only model.
The league-season clears 98% on its own once ≥ 50 matches are finished.
