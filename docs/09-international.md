# 09 — International football (addendum, user decision 2026-09-27)

Added at the user's request after Phase 2. Where this file and docs/01–08
disagree **for international matches**, this file wins; everything else is
unchanged.

## 1. Competition
- New competition key `INTL` ("International (men's A)"). No football-data.co.uk
  code, no Understat key → goals-only model (`xg_blend_weight` treated as 1.0).
- Training data: **all** men's A internationals — friendlies and competitive
  (user decision), with the usual time decay and `train_seasons_back × 365` window.

## 2. Data source (verify before coding → `docs/discovered/international-results.md`)
- Candidate: the public dataset `martj42/international_results` (GitHub),
  `results.csv` with date, home/away team, scores, tournament, venue, `neutral`.
- Results only: **no odds, no xG**. Same caching / politeness rules as other sources.
- Canonical team names = this dataset's spelling (it plays football-data's role for INTL).

## 3. Model change: neutral venues
- `matches.neutral` (boolean, default false) via Alembic migration.
- Home advantage `h` applies only when `neutral = false`:
  `log λ = γ + h·(1 − neutral) + att[i] + def[j]`. Club leagues are unaffected
  (all their matches are non-neutral).
- One Dixon-Coles fit over all national teams (the whole INTL pool is one "league").

## 3a. Schema consequences (discovered 2026-09-27, see docs/discovered/international-results.md)
- National teams can meet at the same home venue more than once per season, so INTL
  matches cannot use the club key `UNIQUE(league, home, away, season)`. Migration adds
  `matches.fixture_key TEXT NOT NULL DEFAULT ''` and the key becomes
  `UNIQUE(league, home, away, season, fixture_key)`: clubs keep `''` (unchanged
  behaviour), INTL rows use the match date.
- `leagues.fd_code` becomes nullable (INTL has none).
- The dataset has no kickoff times: INTL results are stored at 12:00 UTC of the listed
  date; results are matched to SportyBet fixtures by (home, away, date ± 1 day).
- National-team names that clash with a club canonical name get the suffix
  " (national team)" (only `Monaco` today).

## 4. Validation
- No historical odds → the backtest reports calibration and log loss only for INTL;
  ROI, CLV and the gate are **not computable** and are shown as such.
- INTL is permanently labelled **unvalidated** in reports and the dashboard until
  paper-trading CLV exists.

## 5. Slips (user decision)
- INTL legs are priced and stored like any other, labelled "unvalidated".
- INTL legs form **INTL-only slips**; they are **never mixed** with club-league legs
  in the same slip.
- Paper mode default as everywhere.

## 6. Build order
1. Phase 3 discovery covers the 5 leagues **and** international fixtures on SportyBet.
2. INTL data source discovery → ingest → neutral-venue model → fit → calibration report.
3. Remainder of Phase 3 (client, parser, team mapping, `make odds`) for all 6 competitions.
