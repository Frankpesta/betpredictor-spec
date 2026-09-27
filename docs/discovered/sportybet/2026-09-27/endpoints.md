# SportyBet (Nigeria) — discovered endpoints and facts

Verified **2026-09-27**. Sources:
- headed session with the user (`make discover`): `data/raw/sportybet/2026-09-27/discovery/` (182 JSON responses);
- two plain-`httpx` probes: `…/probes/`;
- a short headless recorder session (F9 page render, results page): `…/discovery2/`.

Trimmed samples are in this folder (`event_arsenal_leeds.json` corrected 2026-09-27: it first held the Aston Villa v Brentford payload by mistake); the same (less trimmed) payloads are the offline
test fixtures in `engine/tests/fixtures/sportybet/`.

## Transport
- Plain `httpx` works for the read endpoints (probe: `GET factsCenter/event` → 200,
  `bizCode 10000`, 302 markets; `POST factsCenter/pcEvents` → 200). **No Playwright
  needed for odds.** Playwright-in-page stays as the documented fallback transport.
- Headers the site sends (and our client sends): `clientid: web`, `operid: 2`,
  `platform: web`, `accept-language: en`, `content-type: application/json;charset=UTF-8`
  for POSTs, a desktop user agent, a `referer` on `https://www.sportybet.com/ng/...`.
  No cookies or login were needed for any call below.
- Every response is JSON `{"bizCode": 10000, "message": "...", "data": ...}`;
  `bizCode != 10000` must be treated as an error.

## Facts F1–F12

| # | fact | finding |
|---|---|---|
| F1 | upcoming events by tournament | **`POST https://www.sportybet.com/api/ng/factsCenter/pcEvents`**, body `[{"sportId":"sr:sport:1","marketId":"1,18,16","tournamentId":[["sr:tournament:17", …]]}]` → `data` = list of tournaments, each with `events[]` carrying the requested markets with **all their lines**. The web page asks for `1,18,10,29,11,26,36,14` (no AH); adding **`16` returns every AH line** (probe). No pagination observed: 20 events per league (≈ 2 rounds), 33 for the Nations League. Alternative (paged): `GET factsCenter/pcUpcomingEvents?sportId=sr:sport:1&marketId=…&pageSize=100&pageNum=1&option=1` → `data.totalNum`, `data.tournaments`. |
| F2 | league ids | Sportradar tournament ids: **EPL `sr:tournament:17`, LaLiga `sr:tournament:8`, Serie A `sr:tournament:23`, Bundesliga `sr:tournament:35`, Ligue 1 `sr:tournament:34`**. International (men's A, category `sr:category:4`): UEFA Nations League `23755`, AFCON Qualification `1848`, Int. Friendly Games `851`, CONCACAF Nations League `27420`, Gulf Cup `622`, FIFA ASEAN Cup `53324` (also `1` Euro 2028 and `16` World Cup 2030 with 1 event each, and women's `26006`, `290` — excluded). Club competitions are a separate category `sr:category:393` (Champions League etc.) and are **not** INTL. Source: `GET factsCenter/popularAndSportList?sportId=sr:sport:1&timeline=&productId=3`. |
| F3 | event id | `eventId` = `sr:match:<n>` (e.g. `sr:match:72221292`) → `matches.sportybet_event_id`. Also a short `gameId` ("29490", shown on the page as "ID:29490"). |
| F4 | teams, kickoff | `homeTeamName` / `awayTeamName` (SportyBet spellings, e.g. "Leeds United", "Parma Calcio", "Malaga CF", "Athletic Bilbao"), plus `homeTeamId`/`awayTeamId` `sr:competitor:<n>`. Kickoff `estimateStartTime` = **Unix epoch milliseconds (an absolute UTC instant)**: Arsenal v Leeds `1791631800000` = 2026-10-10 11:30 UTC; the page (clock "GMT+01:00") shows "Saturday 10/10 12:30" = Lagos. |
| F5 | Asian Handicap | market **`id "16"`**, `desc "Asian Handicap <x>"`, `specifier "hcp=<x>"`, `group "Main"`, exactly **two** outcomes. Full time only — first/second-half AH are other ids (`66` "1st Half - Asian Handicap", and a 2nd-half market) and are excluded. Market **`14` is "Handicap 0:1" etc., specifier `hcp=0:1`, three outcomes incl. Draw = European 3-way handicap — excluded.** |
| F6 | Over/Under | market **`id "18"`**, `desc "Over/Under"`, `specifier "total=<x>"`, two outcomes, full time. Lines seen: 0.5–7 incl. whole lines (1, 2, 3 …). Excluded look-alikes: `19`/`20` (home/away team totals), `68` (1st-half O/U), `36` (another O/U-combo market). |
| F7 | outcome ids | AH (16): **`1714` = Home, `1715` = Away** (`desc "Home (-0.5)"`, `"Away (+0.5)"`). O/U (18): **`12` = Over, `13` = Under**. Odds are strings of decimal odds (`"1.41"`). |
| F8 | active / suspended | market `status` `0` + outcome `isActive` `1` on every line shown on the page. **Observed non-active case:** in the `pcEvents` probe, Arsenal v Leeds `hcp=-3.5` had market **`status: 2`** while its outcomes still said `isActive: 1`, and that line was *not* shown on the event page at discovery time → `status != 0` means not offered/suspended regardless of `isActive`. The parser treats anything other than `status == 0` and `isActive == 1` as not active (counted, discarded). Event-level `status` 0 = not started; `matchStatus` "Not start". Also `banned: false` at event and market level — `banned: true` is treated as inactive. |
| F9 | AH sign | **`hcp` is the home team's handicap.** Arsenal (home, 1X2 1.44 v 7.24) — payload `hcp=-0.5`: `1714` "Home (-0.5)" 1.41, `1715` "Away (+0.5)" 2.80; rendered page shows exactly "Home (-0.5) 1.41 / Away (+0.5) 2.80" (`F9_asian_handicap.png`, `F9_header.png`). So **line (home perspective) = float(hcp)** for both outcomes; no sign flip. |
| F10 | all lines | Both `factsCenter/event` and `pcEvents` (with `16`/`18` in `marketId`) return **every** line of the market as separate market objects (same `id`, different `specifier`). Per-event detail: `GET factsCenter/event?eventId=sr:match:<n>&productId=3` (all ~300 markets). |
| F11 | booking | **`POST https://www.sportybet.com/api/ng/orders/share`**, body `{"selections":[{"eventId","marketId","specifier","outcomeId"}, …]}` → `data.shareCode` (e.g. **`X6YP10`**), `data.shareURL`, `data.deadline` (epoch ms), echoed `ticket.selections`, current `outcomes` with odds. **Works without login** (`userId: ""`) → docs/04 §3.2 preferred path. The page validates selections first with `POST factsCenter/Outcomes` (body: list of `{eventId, marketId, outcomeId, specifier}` → current odds + `isActive`), which is also our odds-drift check (§3.4). Sample: `orders_share.json`. |
| F12 | results | **`GET factsCenter/eventResultList?pageNum=1&pageSize=100&sportId=sr:sport:1&startTime=<ms>&endTime=<ms>`** (a Lagos calendar day) → `data.totalNum` (1,069 football results on 2026-09-26), `data.tournaments[].events[]` with `eventId`, `status` (**3 and 4 both seen, both `matchStatus` "Ended"**), `setScore`, `gameScore` (one entry per period), `regularTimeScore`. **CORRECTED 2026-09-27: `regularTimeScore` is NOT the 90-minute score** — it equals the first-half score: USA v Peru `setScore 4:1`, `gameScore [1:1, 3:0]`, `regularTimeScore [1:1]`; Tenerife v Cadiz `1:1`, `[1:0, 0:1]`, `[1:0]`. `setScore` includes extra time and penalties (Charleroi v Habay cup: `3:4`, periods `[1:0,0:1,0:0,0:0,2:3]`). **90-minute score = gameScore[0] + gameScore[1]**; when `gameScore` is missing (12 of 100 results, e.g. FA Trophy) SportyBet data cannot settle the match. Statuses for postponed/cancelled/abandoned: **not yet observed**. **Filter:** `&tournamentId=sr:tournament:23755` works (probe: 10 results instead of 1,069, one tournament); a comma-separated list is rejected (`bizCode 19000 "Invalid"`) → one request per tournament per day. |

## Other facts
- **Max selections per betslip: 50** — confirmed by the user for real betslips
  (2026-09-27); matches `maxSelection: 50` in `GET sportySim/v1/config/overall`.
  Our mega accumulator cap (30) is below it.
- Live events (`GET factsCenter/liveOrPrematchEvents?sportId=sr:sport:1`) carry
  `status 1`, `setScore`, `playedSeconds`; `make odds` only uses pre-match events.
- Site clock shown to users: "GMT+01:00" (Lagos).

## Not used / out of scope
Bet placement endpoints, login, wallet, promotions, SportySim — never called by the engine.
