# 02 — Data Pipeline (history, xG, team names)

Goal: a clean `matches` table with goals (and xG where available) for every
enabled league over `history_seasons`, plus `historical_odds` for backtests,
with **every team name resolved** to one canonical team.

---

## 1. football-data.co.uk (results + historical odds)

### 1.1 Source
- URL pattern: `https://www.football-data.co.uk/mmz4281/{SSSS}/{CODE}.csv`
  where `SSSS` is the season code, e.g. season `2024-25` → `2425`.
- One CSV per league per season. Current season files are updated roughly
  twice a week, so the latest few days may be missing — that is expected.

### 1.2 Verify before coding (write to `docs/discovered/football-data.md`)
Download one current-season and one older-season file for E0. Record:
- the full header row of each,
- the date format actually present (`dd/mm/yyyy` and `dd/mm/yy` both occur
  historically — handle both),
- which of the columns below exist in each.

### 1.3 Columns to use (only if present — never crash on absence)
| purpose | columns (expected names) |
|---|---|
| identity | `Div`, `Date`, `Time` (may be absent in old seasons), `HomeTeam`, `AwayTeam` |
| result | `FTHG`, `FTAG` |
| O/U 2.5 opening | `B365>2.5`, `B365<2.5`, `P>2.5`, `P<2.5`, `Avg>2.5`, `Avg<2.5` |
| O/U 2.5 closing | `B365C>2.5`, `B365C<2.5`, `PC>2.5`, `PC<2.5`, `AvgC>2.5`, `AvgC<2.5` |
| AH opening | `AHh` (home handicap), `B365AHH`, `B365AHA`, `PAHH`, `PAHA`, `AvgAHH`, `AvgAHA` |
| AH closing | `AHCh`, `B365CAHH`, `B365CAHA`, `PCAHH`, `PCAHA`, `AvgCAHH`, `AvgCAHA` |

If discovery shows different spellings, map them in one dict
`FD_COLUMN_MAP` in `ingest/football_data.py` and document it. Nothing else
in the codebase may refer to raw CSV column names.

### 1.4 Parsing rules
- Read with `encoding="latin-1"` fallback if UTF-8 fails; strip BOM.
- Drop fully empty trailing rows (they exist). Count and log them.
- `kickoff_utc`: combine `Date` + `Time` as **UK local time**
  (`Europe/London`) for all leagues, then convert to UTC. Confirm the
  timezone in the site's `notes.txt` during discovery and record it; if it
  differs, change only this one conversion. If `Time` is missing, use 15:00 UK and set a flag in logs.
- Odds values ≤ 1.0 or non-numeric → treat as missing.
- `AHh` is the handicap **applied to the home team** (e.g. `-0.75`). Store as
  `line` with selection `home` and `away` both referencing the home-perspective
  line. Verify sign convention on 5 rows by eye during discovery (a strong
  favourite at home should have a negative `AHh`) and record it.
- Status `finished` when both goals present.
- `fd_row_hash` = sha256 of `Div|Date|HomeTeam|AwayTeam` → upsert key.

### 1.5 Output
Upsert into `matches` and `historical_odds`. Log counts per file:
rows read, inserted, updated, skipped-empty, skipped-unresolved-team.

---

## 2. Understat (xG) — top five leagues only

### 2.1 Verify before coding (write to `docs/discovered/understat.md`)
Understat's page/data structure has changed over time. Before choosing an
approach, fetch one league-season page and determine how match data is
delivered (embedded JSON in a `<script>` using `JSON.parse('...')` with
hex-escaped content, or a JSON endpoint called by the page). Record a
trimmed sample. Options, in order of preference:
1. The maintained `understat` PyPI package, **if** it still works against the
   live site (test it on one league-season).
2. Your own adapter using whatever the discovery found.

### 2.2 Fields needed per match
Understat match id, datetime, home team title, away team title, home goals,
away goals, home xG, away xG, `isResult`.

### 2.3 Joining to `matches`
Join on (league, season, resolved home team, resolved away team). Because a
pair plays once at home per season this key is unique. If Understat goals
disagree with football-data goals for the same match, keep football-data
goals, still store xG, and log the mismatch. Report the join rate per
league-season; **it must be ≥ 98%** for finished matches or the phase fails.

### 2.4 If Understat is unreachable
The system must still work: leagues without xG simply use the goals-only
model (`xg_blend_weight` treated as 1.0). Log clearly; never block.

---

## 3. Team-name resolution (the #1 source of silent bugs)

Every source spells teams differently ("Man United", "Manchester United",
"Man Utd", "Wolverhampton Wanderers", "Wolves"...). A single wrong mapping
silently corrupts predictions, so this is strict.

### 3.1 Rules
1. **Canonical name = football-data.co.uk spelling.** Teams are created only
   by the football-data ingest.
2. Every other source name must resolve through `team_aliases`
   `(source, alias)` → `team_id`. Exact match only at runtime.
3. Seed file `engine/src/engine/mapping/aliases_seed.toml` holds known
   aliases per league. You create the initial version by running discovery on
   Understat and SportyBet names and proposing matches.
4. `make map-teams` does the following:
   - collects every distinct raw name seen from Understat and SportyBet;
   - resolves exact alias matches;
   - for the rest, proposes candidates using normalised fuzzy matching
     (lowercase, strip accents, remove `fc`, `afc`, `cf`, `sc`, `ac`, `.`,
     `&`→`and`; then `rapidfuzz.fuzz.token_set_ratio`), restricted to teams
     in the same league;
   - **never auto-accepts** a fuzzy match. It prints a table
     `raw_name | league | best candidate | score` and writes proposals to
     `data/reports/alias_proposals.toml`. The user (or you, after showing the
     user) approves by copying into `aliases_seed.toml`, then re-runs.
5. Unresolved names go to `unresolved_names`. Any SportyBet event whose home
   or away team is unresolved is **excluded from picks** and shown on the
   dashboard "Data health" page.

### 3.2 Known tricky pairs to check explicitly
Man United / Man City; Nott'm Forest; Wolves; Sheffield United vs Sheffield
Weds (E1); Inter / Inter Milan; AC Milan / Milan; Ath Madrid / Atletico /
Atlético Madrid; Ath Bilbao / Athletic Club; Betis / Real Betis; Sociedad /
Real Sociedad; Paris SG / PSG / Paris Saint-Germain; Dortmund / Borussia
Dortmund; M'gladbach / Borussia M.Gladbach; Leverkusen / Bayer Leverkusen;
Ein Frankfurt / Eintracht Frankfurt; FC Koln / Köln / Cologne; Hertha;
Bayern Munich / Bayern München; Celta / Celta Vigo; Espanol / Espanyol;
Alaves / Alavés; Cadiz / Cádiz; Verona / Hellas Verona; St Etienne /
Saint-Etienne; Brest / Stade Brestois.

Unit test: every pair above that exists in the data resolves to the correct
single team, and Man United never resolves to Man City (and vice versa).

---

## 4. Ingest job acceptance
- All enabled leagues × history seasons ingested; counts printed.
- 0 duplicate matches (`SELECT ... GROUP BY natural key HAVING count>1` = 0).
- 0 unresolved names among finished matches used for training.
- Understat xG join rate ≥ 98% per league-season (top five leagues).
- Spot check: print 5 random matches per league with goals, xG, and B365
  O/U 2.5 odds; show user.
