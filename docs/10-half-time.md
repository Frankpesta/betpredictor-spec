# 10 — Half-time model and markets

User request 2026-09-30 (planned), started 2026-10-08. Decisions (2026-10-08):
**split the full-time model** (not a separate half-time fit), a **calibration gate**
before any half-time market enters slips (then labelled *unvalidated*), **club leagues
only** (the INTL results source has no half-time scores).

SportyBet ids for half-time markets: `docs/discovered/sportybet/2026-10-08/new-markets.md`.

## 1. Data

- football-data columns `HTHG` / `HTAG` (first-half goals; verified in
  `docs/discovered/football-data.md` headers) are stored in
  `matches.ht_home_goals` / `matches.ht_away_goals` (migration 0009, nullable).
- A row whose half-time goals exceed its full-time goals, or that has only one of the
  two, stores NULL for both and is counted (`fd_bad_ht_goals`); never silently dropped.
- Second-half goals = full time − half time.
- Cached CSVs (2026-10-08): 8,575 top-5-league matches, 1 without half-time goals,
  first-half share of goals 0.440–0.458 per league (home 0.439–0.459, away 0.441–0.462).

## 2. Model (`model/half_time.py`, pure)

For a match with full-time rates λ (home), μ (away) from the existing model
(`LeagueModel.rates`, same `xg_blend_weight`):

- **Shares** per league, fitted on the same training window as the full-time model:
  `s_home = Σ HTHG / Σ FTHG`, `s_away = Σ HTAG / Σ FTAG` over finished matches with
  half-time goals (no time decay). Fallback when the window has < `half_time.min_matches`
  matches with half-time data: no half-time model for that league (report it).
- First half: λ₁ = s_home·λ, μ₁ = s_away·μ. Second half: λ₂ = (1 − s_home)·λ,
  μ₂ = (1 − s_away)·μ. The halves are independent.
- **Low-score correction** ρ₁ (first half) and ρ₂ (second half): the Dixon–Coles τ on
  each half's score, each fitted by maximum likelihood over the training window (rates
  from the fitted model for those matches) with the bounds `model.rho_bounds`
  (`scipy.optimize.minimize_scalar`, bounded). A τ(0,0) ≤ 0 is impossible inside the
  bounds for realistic half rates; the score-matrix check still raises if it happens.
- Half score matrices: `score_matrix(λ₁, μ₁, ρ₁)`, `score_matrix(λ₂, μ₂, ρ₂)`.
  Every half market is priced with `model.markets.price_selection` on the half matrix —
  the same settlement code, applied to that half's score.

Stored with each club `model_runs.params_json` under `"half_time"`:
`{"share_home", "share_away", "rho_1h", "rho_2h", "n_matches"}`.

## 3. Markets (codes: full-time code + `_1H` / `_2H`)

`OU_1H`, `OU_2H`, `AH_1H`, `AH_2H`, `1X2_1H`, `1X2_2H`, `DC_1H`, `DC_2H`, `BTTS_1H`,
`BTTS_2H`, `OU_HOME_1H`, `OU_AWAY_1H`, `OU_HOME_2H`, `OU_AWAY_2H` — SportyBet ids 68, 90,
66, 88, 60, 83, 63, 85, 75, 95, 69, 70, 91, 92. Settled on the first-half score
(`gameScore[0]`) or the second-half score (`gameScore[1]`) — football-data HT goals as
the secondary source. Implementation of odds/picks/settlement for these codes waits for
the gate (§4) and a pcEvents probe of those ids.

## 4. Backtest and gate (`backtest/half_time.py`, `make backtest-ht`)

No historical half-time odds exist, so this measures **calibration only, never ROI**.

- Walk-forward over `backtest.test_seasons`, refit every `backtest.refit_every_days`, the
  tuned `xi_per_day` / `xg_blend_weight`; shares and ρ refitted per block from the
  block's training window (leakage asserted like docs/03 §10.1).
- Markets scored, for each half: O/U 0.5, 1.5, 2.5 (over), 1X2 (three outcomes),
  BTTS (yes), home-team over 0.5, away-team over 0.5. Double chance and AH ±0.5 are
  sums of 1X2 outcomes and pass/fail with 1X2.
- Metrics per market and half: n, log loss, Brier, ECE (10 equal-width bins; 1X2 uses
  the three one-vs-rest outcomes pooled), and the **baseline** log loss of a model with
  no team strength (league-average full-time rates of the training window × the same
  shares and ρ).
- **Gate per market and half:** ECE ≤ `gates.max_calibration_ece` (0.03) **and** log loss
  below the baseline. Passing markets may then be added to `value.markets` (user
  approval) and enter slips labelled *unvalidated*; failing ones stay out.
- Output: `data/reports/half_time_backtest_<timestamp>.md` + `.json`, and a `job_runs`
  row (`backtest_ht`).

## 5. Tests

`tests/test_half_time.py`: shares on a table, ρ recovery on simulated half scores,
half matrices sum to 1 and the two halves' totals convolve to the full-time Poisson
total when ρ = 0, gate logic table, leakage assertion. Ingest: HT goals parsed, bad
HT rows counted.

## 6. First backtest (2026-10-08, `data/reports/half_time_backtest_2026-10-08_1221.md`)

5,237 club matches (2023-24 … 2025-26, 5 leagues), refit every block, tuned settings.
**13 of 14 pass**; **2H GG/NG fails** (log loss 0.5912 vs baseline 0.5910 — no better than
league average). ECE 0.005–0.019 everywhere. Gains over the baseline are small
(e.g. 1H O/U 0.5: 0.5875 vs 0.5926); the biggest are 1X2 (1H 1.037 vs 1.083) and team
goals. Fitted first-half shares 0.432–0.470; ρ 1H −0.06…+0.02, 2H +0.01…+0.04.
User approved wiring the 13 passing markets (2026-10-08) — see §7.

## 7. Live wiring (2026-10-08, user approval)

- **Discovery:** pcEvents probe of all half-time ids (EPL, 20/20 events), AH half `hcp` =
  home line (240 outcomes checked) — `docs/discovered/sportybet/2026-10-08/new-markets.md`.
- **Codes / DB:** migration 0010 widens `market` CHECKs to the `_1H`/`_2H` codes.
  `model.markets.split_period` / `base_market`; selections are the full-time ones.
- **Parser:** `MARKET_SPECS` gains ids 60, 63, 66, 68, 69, 70, 75 (1st half) and 83, 85,
  88, 90, 91, 92 (2nd half). **95 (2H GG/NG) is not parsed** (failed the gate). Lines kept
  only where the backtest checked them: half O/U 0.5–2.5, half team goals 0.5, half AH ±0.5
  (`HALF_LINE_RANGES`); others count as `line_out_of_range`.
- **Model runs:** club `fit_and_store` stores `"half_time"` (shares, ρ) via
  `model.half_time.fit_for_model` (same helper as the backtest); INTL stores null. Club runs
  saved before this have no key and are refitted by `make picks`.
- **Picks:** half markets priced from `half_matrix`; enabled through `value.markets`;
  skipped with `no_half_time_model` when a league has none.
- **Data rule for halves** (`[value.data_rule_half]`): the docs/05 §9 rule on one half —
  P(score) = model chance the team scores *in that half*, blanks = games it did not score
  in that half (last 10 with half-time data). Thresholds: strong ≥ 65% & ≤ 4 blanks; weak
  ≤ 35% & ≥ 7 blanks; Over/GG both ≥ 55% & ≤ 5 blanks; odds ≥ 1.20, p ≥ 50%. Reasons are
  prefixed "1st half: " / "2nd half: ".
- **Settlement:** `SbResult.score_1h` = `gameScore[0]` (only when the halves add up to the
  final score); reconciled with football-data HT goals (disagreement → `needs_review`);
  2nd half = full time − first half; a half leg waits while its half score is unknown.
- **Dashboard:** "1st half · Over/Under" labels; every half-time leg is *unvalidated*.
- First live run: 5,276 legs priced, 103 qualify (45 before), half-time model–market gap
  similar to full time (mean 3–8 pp). Half team-goal legs carry clearly negative edge
  (−6 … −10 %): SportyBet's margin on them is high.
