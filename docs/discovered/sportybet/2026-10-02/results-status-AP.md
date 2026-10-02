# SportyBet result status "AP" (observed 2026-10-02)

Source: `GET factsCenter/eventResultList` for 2026-10-01 (Lagos day), tournament
`sr:tournament:851` (Int. Friendly Games). Raw cache:
`data/raw/sportybet/2026-10-02/results_sr_tournament_851_2026-10-01_p1.json`.
Sample: `eventResult_AP_japan_ecuador.json`.

| field | value |
|---|---|
| eventId | sr:match:74764066 (Japan v Ecuador, kickoff 2026-09-30) |
| status | 4 |
| matchStatus | **"AP"** (not "Ended") |
| setScore | 5:4 (includes the penalty shoot-out) |
| gameScore | ["0:0", "0:0", "5:4"] (1H, 2H, penalties) |
| regularTimeScore | ["0:0", "0:0"] |

Reading: "AP" = after penalties (a friendly decided by a shoot-out, no extra-time
period in `gameScore`). Per endpoints.md the 90-minute score is gameScore[0] +
gameScore[1] = **0:0**.

Decision (user, 2026-10-02): **"AP" counts as ended**, settled on the 90-minute
score. `sportybet/results.py` ENDED_MATCH_STATUSES = {"Ended", "AP"} (status 3/4).
Any other matchStatus (e.g. a future "AET", postponed/cancelled labels) is still
reported as `unrecognised SportyBet status` and left pending — never guessed.
