# International results dataset — discovered facts

Verified **2026-09-27**. Raw copy: `data/raw/international_results/2026-09-27/results.csv`.

## Source
- `https://raw.githubusercontent.com/martj42/international_results/master/results.csv`
  → 200, `text/plain; charset=utf-8`, 3.7 MB, one file for all history.
- Header (verbatim): `date,home_team,away_team,home_score,away_score,tournament,city,country,neutral`
- 49,547 rows, 1872-11-30 … **2026-08-26** (last update before this date's window:
  the dataset lags by weeks; September 2026 internationals are not in it yet).
- `date` = `YYYY-MM-DD`, the **local** match date. **No kickoff time.**
- `neutral` = `TRUE` / `FALSE` (13,158 / 36,389).
- Scores are integers; 0 rows with missing scores (the file holds results only,
  no scheduled fixtures).

## Coverage (last 3 years, from 2023-09-28)
3,069 matches, 240 teams (26 with < 8 matches). Top tournaments: FIFA World Cup
qualification 891, Friendly 743, UEFA Nations League 186, CONCACAF Nations League 177,
AFCON qualification 152, AFCON 104, FIFA World Cup 104 (all 2026 finals matches present),
UEFA Euro qualification 101. Includes some non-FIFA sides (e.g. Monaco, Jersey).

## Facts that shape the schema (see docs/09 §3a)
- **Repeat fixtures:** since 2016-07, 372 (season, home, away) keys occur more than once
  (393 extra rows), e.g. Honduras v Nicaragua 3× in 2016-17. The club key
  `UNIQUE(league, home, away, season)` cannot hold for INTL → INTL rows carry
  `fixture_key` = match date.
- 1 exact same-day duplicate (date, home, away) — counted and skipped at ingest.
- **Name clash with club canonical names:** `Monaco` (also a Ligue 1 club). Clashing
  national-team names are stored as `"<name> (national team)"`.

## Kickoff time
Unknown in this source → stored as **12:00 UTC on the listed date**. A fixture first
created from SportyBet keeps SportyBet's exact kickoff; the dataset result is matched to
it by (home, away, date ± 1 day) because local and UTC dates can differ.
