# 06 — Settlement, CLV and Performance Tracking

Code: `engine/src/engine/settle/`.

## 1. Closing-odds snapshot (`make close`)

Closing line value (CLV) is the best early signal of whether the model has
an edge: if the odds you took are consistently better than the price just
before kickoff, you are likely beating the market long before profit proves it.

- `make close` fetches SportyBet odds (same client as `make odds`) **only for
  matches that appear in open slips** and kick off within the next 90
  minutes, storing them with `snapshot_kind='close'`.
- Since the tool runs manually, the user runs `make close` (or clicks
  "Snapshot closing odds" on the dashboard) roughly 30–60 minutes before the
  main kickoff times. Missing close snapshots are normal; CLV is then NULL
  for those legs, never guessed.
- For each slip leg with a close snapshot of the same market, line and
  selection: `closing_odds = that odds`, `clv = odds_taken / closing_odds − 1`.
  If the exact line is no longer offered, leave CLV NULL.

## 2. Settling legs (`make settle`)

For every `slip_legs.result = 'pending'` whose match kicked off more than
2.5 hours ago:
1. Obtain the full-time score via docs/04 §4.
2. If the match is postponed/cancelled/abandoned → `void`, multiplier 1.
3. If `needs_review` → skip, leave pending, report.
4. Otherwise settle with the **same** functions from docs/03 §8 and store
   `result` and `result_multiplier` (using the leg's taken odds).

## 3. Settling slips

When every leg of a slip is settled:
- `return_multiplier = Π result_multiplier_k`
- `status`:
  - all legs `void` → `void`
  - `return_multiplier == 0` → `lost`
  - every non-void leg is `win` → `won`
  - otherwise (some half results, return > 0) → `partial`
- A slip with any `loss` leg is immediately `lost` without waiting for the
  remaining legs (return 0 regardless), but keep settling its other legs for
  per-leg statistics.

Note: SportyBet's own treatment of void legs and half results in
accumulators must be confirmed in discovery (docs/04). If it differs from
the above, record it in `docs/discovered/` and match SportyBet's rules
exactly — the tracker must reflect what SportyBet would actually pay.

## 4. Performance metrics (computed on read, via SQL views)

Create these as SQLite views in an Alembic migration so both the engine and
the dashboard read identical numbers:

- `v_leg_performance`: per settled leg — league, market, line, selection,
  odds, p_final, edge at pick, result, multiplier, profit (multiplier − 1),
  clv, pick date, model_version.
- `v_slip_performance`: per settled slip — type, date, legs, total odds,
  p_all_win, expected_multiplier, status, return_multiplier, profit, mode,
  stake, model_version.
- Aggregations the dashboard needs (computed in queries or views):
  - legs: count, hit rate (win + half_win counts as 0.5 win), flat-stake ROI
    `= Σprofit / count`, mean CLV, split by market, league, odds band
    (1.20–1.50, 1.50–1.80, 1.80–2.20, 2.20–2.60), and month;
  - calibration of settled legs: reliability table of `p_final` vs outcome
    (reuse `calibration.reliability_table`) and ECE;
  - slips: count, hit rate, ROI per slip type; for `placed` slips also
    real profit in Naira using `stake`;
  - equity curve: cumulative flat-stake profit of legs and of each slip type
    over time;
  - expected vs actual: Σ(expected_multiplier − 1) vs Σ profit per slip type
    (tells you whether results are luck or model).

## 5. Tests
- Settlement of slips with combinations of win/half_win/push/void/loss
  produces the correct multiplier and status, e.g. legs
  (win @1.5, half_win @1.8, void) → 1.5 × 1.4 × 1 = 2.1 → `partial`.
- CLV: odds taken 1.90, closing 1.80 → 0.0556 (±1e-4).
- A match with conflicting results from the two sources is never settled.
