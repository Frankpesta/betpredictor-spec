# SportyBet — extra goal markets (discovered 2026-10-08)

User request 2026-10-08: add team goals, both teams to score, 1X2 + double chance
(full time) and half-time markets. Verified with two requests through the engine's
polite client (raw responses cached under `data/raw/sportybet/2026-10-08/discover_*`):

1. `GET factsCenter/event?eventId=sr:match:72221296&productId=3` (Chelsea v Bournemouth)
   → 719 market objects, 239 distinct ids. Full list: `event_market_inventory.tsv`.
2. `POST factsCenter/pcEvents` body
   `[{"sportId":"sr:sport:1","marketId":"1,10,29,19,20,16,18","tournamentId":[["sr:tournament:17"]]}]`
   → 20 EPL events; **every event carried markets 1, 10, 19, 20 and 29** (all `status 0`,
   outcomes `isActive 1`). Trimmed sample: `pcEvents_epl_new_markets.json`.

So the existing `make odds` request (F1) only needs the extra ids in `marketId`.

## Full-time markets (verified in both responses)

| id | desc | specifier | outcomes (id = desc) |
|---|---|---|---|
| `1` | `1X2` | none (`""`) | `1` = Home, `2` = Draw, `3` = Away |
| `10` | `Double Chance` | none | `9` = Home or Draw, `10` = Home or Away, `11` = Draw or Away |
| `19` | `<home team name> Over/Under` (e.g. "Chelsea Over/Under") | `total=<x>` (0.5–5.5 seen, .5 only) | `12` = Over, `13` = Under |
| `20` | `<away team name> Over/Under` | `total=<x>` (0.5–4.5 seen, .5 only) | `12` = Over, `13` = Under |
| `29` | `GG/NG` | none | `74` = Yes, `76` = No |

Notes:
- `19`/`20` desc carries the **team name**, not "Home"/"Away": identify by market id; the
  home/away side is fixed by the id (19 = home team, 20 = away team).
- Lines per event: team totals 0.5 / 1.5 / 2.5 almost always, 3.5–5.5 for some.
- `marketGuide` for 29: "both teams will score in the match at regular time" — 90 minutes,
  same as AH/OU (F12: 90-minute score = `gameScore[0] + gameScore[1]`).
- Related but **not** adopted: `11` Draw No Bet (= AH 0, outcomes `4`/`5`), `31`/`32` clean
  sheet, `33`/`34` win to nil, `35`–`37`, `546`, `547` combos, `60000` "GG/NG 2+",
  `60100`/`60110`/`60200`/`60210` early-payout variants (different settlement — never use).

## Half-time markets (seen in the event detail only; not yet in a pcEvents probe)

| id | desc | specifier | outcomes |
|---|---|---|---|
| `60` | 1st Half - 1X2 | none | `1`/`2`/`3` |
| `63` | 1st Half - Double Chance | none | `9`/`10`/`11` |
| `66` | 1st Half - Asian Handicap | `hcp=<x>` (−1.5…0.5, incl. whole) | `1714`/`1715` |
| `68` | 1st Half - Over/Under | `total=<x>` (0.5–3) | `12`/`13` |
| `69` / `70` | 1st half - <home/away team> Over/Under | `total=<x>` | `12`/`13` |
| `75` | 1st Half - GG/NG | none | `74`/`76` |
| `83` | 2nd Half - 1X2 | none | `1`/`2`/`3` |
| `85` | 2nd Half - Double Chance | none | `9`/`10`/`11` |
| `88` | 2nd Half - Asian Handicap | `hcp=<x>` | `1714`/`1715` |
| `90` | 2nd Half - Over/Under | `total=<x>` (0.5–3) | `12`/`13` |
| `91` / `92` | 2nd Half - <home/away team> Over/Under | `total=<x>` | `12`/`13` |
| `95` | 2nd Half - GG/NG | none | `74`/`76` |

Half-time markets need a half-time goals model + backtest (football-data HTHG/HTAG)
before they enter slips (docs/05 §8.5); settle from `gameScore[0]` (1st half) and
`gameScore[1]` (2nd half).

**pcEvents probe 2026-10-08 12:25Z** (`marketId` 60,63,66,68,69,70,75,83,85,88,90,91,92,95,
EPL; raw `discover_pcEvents_EPL_halftime.json`, trimmed sample `pcEvents_epl_halftime.json`):
every id on all 20 events. Descs exactly as in the table; team markets use the short label
("1st half - Nottingham Over/Under" — note lower-case "half" on 69/70, "2nd Half" on 91/92).
66/88 desc is just "1st/2nd Half - Asian Handicap" (no line); `hcp` is the **home** line like
market 16 — all 240 outcome descs checked ("Home (-0.5)" on `hcp=-0.5`), and Arsenal 1H
AH −0.5 @1.91 ≈ 1H 1X2 home @1.89. Lines seen: O/U 0.5–3 (incl. whole), team 0.5–2.5,
AH −1.5…+1.5 (incl. whole/zero).

## Team-goals labels differ from event team names (first `make odds`, 2026-10-08 11:39Z)

Market desc uses SportyBet's own short label, not the event's `homeTeamName`/`awayTeamName`:
`"Nottingham Over/Under"` (event: "Nottingham Forest"), `"ESTAC Troyes Over/Under"` ("Troyes"),
`"Milan Over/Under"` ("AC Milan"). The id fixes the side: Sassuolo v AC Milan has "Milan" on
**20** (away), AC Milan v Atalanta has "Milan" on **19** (home). The parser therefore accepts
any "<label> Over/Under" on 19/20 unless the label is exactly the *other* team's name.
Raw: `data/raw/sportybet/2026-10-08/pcEvents_{EPL,LIGUE1,SERIEA}_1139*.json`.

## Booking lineless selections (verified 2026-10-08)

`POST orders/share` accepts `"specifier": ""` for 1X2 / double chance / GG/NG. Test code
**H4J8R8** (paper test, not a slip): GG/NG Yes (29/74), Double Chance Home or Draw (10/9),
home team Over 0.5 (19/12, `total=0.5`); the echoed `ticket.selections` matched all three
through the normal `book_legs` drift + echo checks. Sample: `share_lineless.json`.

## Booking lineless selections (verified 2026-10-08)

`POST orders/share` accepts `"specifier": ""` for 1X2 / double chance / GG/NG; the echoed
`ticket.selections` carry `"specifier": null` for them (the parser normalises null → "").
Test code **H4J8R8** (paper test, not a slip): GG/NG Yes (29/74), Double Chance Home or
Draw (10/9), home team Over 0.5 (19/12, `total=0.5`) — passed the normal `book_legs`
drift + echo checks. Sample: `share_lineless.json`; raw under
`data/raw/sportybet/2026-10-08/booking/`.
