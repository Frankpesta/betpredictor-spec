# 05 — Value Detection and Slip Building

Code: `engine/src/engine/value/` and `engine/src/engine/slips/`.
All functions pure; DB reads/writes happen in the `picks` job only.

---

## 1. Inputs to `make picks`

1. Latest successful `model_runs` per enabled league (fit if older than
   7 days or older than the newest finished match in `matches`; `make picks`
   refits automatically in that case and logs it).
2. `predictions` for every scheduled match with kickoff in the next 72 hours
   (create if missing for the current model run).
3. The **latest** `odds_snapshots` with `snapshot_kind='pick'` per
   (match, market, line, selection). If the newest snapshot for a match is
   older than 3 hours, warn in the job summary and dashboard ("odds stale —
   run `make odds`").

## 2. Per-selection value computation

For every (match, market, line) where **both** selections are present in the
same snapshot batch:

1. **Line filter (v1):** keep only half lines (`x.5`). Whole and quarter
   lines are priced and stored for information but marked
   `sanity_status='flagged'`, `sanity_reason='non_half_line_v1'`.
2. **Model probability:** price the selection from the score matrix using
   docs/03 §8.5 → `p_model = p_win`.
3. **Market devig (multiplicative):**
   `q_a = 1/o_a, q_b = 1/o_b, p_market_devig_a = q_a / (q_a + q_b)`.
4. **Shrink toward market:**
   `p_final = w·p_model + (1−w)·p_market_devig` with
   `w = value.market_shrink_weight`.
5. **Outcome distribution (half lines):** `p_win = p_final`,
   `p_loss = 1 − p_final`, the rest 0.
6. `expected_multiplier = p_final · o`; `edge = expected_multiplier − 1`.

### 2.1 Sanity checks (any failure → `flagged` with reason; never deleted)
| check | reason code |
|---|---|
| `abs(p_model − p_market_devig) > value.max_model_market_gap` | `model_market_gap` |
| home or away team is `low_confidence` (docs/03 §7) | `low_confidence_team` |
| kickoff is less than 15 minutes away, or in the past | `too_close_to_kickoff` |
| prediction's model run is not the latest for the league | `stale_prediction` |
| overround of the pair outside [1.00, 1.15] | `bad_overround` |
| the opposite selection is missing | `missing_pair` |

A large model/market gap almost always means **our** data is wrong (mapped
to the wrong team, a key injury/lineup news, a stale fit) rather than a
gift from the bookmaker. That is why it is flagged, not favoured.

### 2.2 Qualifying legs
A leg qualifies for slips when **all** hold:
`sanity_status = 'ok'`, `edge ≥ value.min_edge_leg`,
`value.min_odds ≤ odds ≤ value.max_odds`.
Every computed selection (qualifying or not) is written to `value_legs` so
the dashboard can show why something was excluded.

### 2.3 Tests (`tests/test_value.py`)
- Devig: odds 1.90/1.90 → 0.5/0.5; odds 1.50/2.60 → 0.6341/0.3659 (±1e-4).
- Shrink: `p_model=0.70, p_dev=0.60, w=0.7` → `p_final=0.67`.
- Edge: `p_final=0.67, o=1.60` → `expected_multiplier=1.072`, `edge=0.072`.
- Gap flag triggers at 0.151 and not at 0.149 with max gap 0.15.

---

## 3. Shared slip rules (all slip types)

- **One leg per match** in a slip (no two markets from the same match —
  they are correlated and usually rejected by the bookmaker anyway).
- Only qualifying legs from §2.2.
- Legs involving a match with kickoff before the slip is generated + 15 min
  are excluded.
- Slip totals:
  - `total_odds = Π o_k`
  - `p_all_win = Π p_final_k` (legs assumed independent across matches)
  - `expected_multiplier = Π expected_multiplier_k` (exact for independent
    legs: expectation of a product of independent variables is the product
    of expectations)
  - slip edge = `expected_multiplier − 1`
- **Determinism:** identical inputs must give identical slips. Break every
  tie by (higher `p_final`, then higher `edge`, then earlier kickoff, then
  lower `match_id`, then `market`, then `line`, then `selection`).
- Leg counts never exceed SportyBet's maximum selections per betslip
  (docs/04 §3.6).
- **No forcing.** If no combination satisfies the constraints, no slip is
  created for that type/date. The dashboard shows "No qualifying slip —
  the model found no value today". Never relax thresholds automatically.

---

## 4. Daily 2-odds slip (`slip_type = 'daily_2odds'`)

- Window: kickoffs from now + 15 min to now + `window_hours` (24h).
- Candidate pool: qualifying legs with `p_final ≥ min_leg_probability`;
  if more than 60, keep the top 60 by the tie-break order.
- Enumerate every combination of `min_legs..max_legs` legs (2 or 3) from
  distinct matches (use `itertools.combinations`; ≤ ~36k combos for 60).
- Feasible if `target_odds_min ≤ total_odds ≤ target_odds_max` **and**
  slip edge `≥ min_slip_edge`.
- Choose the feasible combination with the **highest `p_all_win`**; ties by
  higher slip edge, then the tie-break order of its legs.
- At most one daily_2odds slip per Lagos date.

Worked example for a test: legs A (o=1.40, p=0.76), B (o=1.42, p=0.75),
C (o=1.35, p=0.78) from different matches. A+B: odds 1.988, p 0.570,
EM 0.570·1.988 = 1.1333 → feasible. A+C: odds 1.89, p 0.5928, EM 1.1204 →
feasible. B+C: odds 1.917, p 0.585, EM 1.1214 → feasible. A+B+C: odds
2.6838 → above 2.30, infeasible. **Expected choice: A+C** (highest p_all_win).

## 5. Mid accumulator (`slip_type = 'mid_acca'`)

- Window: now + 15 min to now + 72h.
- Pool: qualifying legs with `p_final ≥ min_leg_probability`, one per match
  (the best leg per match by tie-break order).
- Greedy: sort pool by tie-break order; add legs one at a time while the
  running slip edge stays `≥ min_slip_edge`; stop at `max_legs`.
- Create only if the result has `≥ min_legs` legs.

## 6. Mega accumulator (`slip_type = 'mega_acca'`)

- Window: the **upcoming or current** weekend window defined by
  `weekend_start`..`weekend_end` in Lagos time, clipped to start no earlier
  than now + 15 min. (Run on Monday–Thursday → next weekend; run during the
  weekend → remaining fixtures only.)
- Pool: legs with `sanity_status='ok'`, `edge ≥ min_leg_edge`,
  `p_final ≥ min_leg_probability`, odds bounds; one per match.
- Take the top legs by tie-break order up to `max_legs`.
- Create with whatever count qualifies (≥ 2). The dashboard must display
  the leg count, `p_all_win` as a percentage with 4 significant figures
  (e.g. "0.01523%"), and `expected_multiplier`. Label: **High risk**.
- One mega slip per weekend window.

## 7. Slip builder tests (`tests/test_slips.py`)
- §4 worked example returns A+C.
- No slip contains two legs from the same match.
- No flagged or non-qualifying leg is ever used.
- daily_2odds total odds always within target range; slip edge ≥ threshold.
- Empty pool → no slip, no exception.
- Same inputs shuffled → identical output (determinism).
- Mega respects `max_legs` and the SportyBet cap.
- Re-running picks for the same date replaces only `pending` & `open` slips
  (integration test with a temp SQLite DB).

---

## 8. Selection strategy: "likeliest" (user decision 2026-09-30)

`value.selection` in `config/settings.toml` picks the rule. The user switched from
`"value"` (§2.2 above) to `"likeliest"`: *"The goal is to win."* They chose pure
likeliest (no odds floor), the 30/70 blend, kept slip types without edge rules,
backing the stronger team on handicaps, and restarting the paper clock.

Honest framing (CLAUDE.md §6): the likeliest leg is usually a short price
(1.02–1.30) that the market prices efficiently; its edge is typically negative
(first run 2026-09-30: about −3% per leg; an 8-leg slip had expected multiplier 0.79). "Likeliest" raises the hit rate, not the expected return. The
dashboard and reports keep showing edge and expected multiplier.

### 8.1 Qualifying legs (`value_legs.qualifies`)
A leg qualifies under "likeliest" when **all** hold:
- `sanity_status = 'ok'` (§2.1 unchanged — half lines only, gap/low-confidence/
  kickoff/stale/overround checks still exclude);
- `market = 'OU'`, **or** `market = 'AH'` and the selection is the match
  **favourite's** side (any line: favourite −1.5, −0.5 "to win", +0.5 "win or
  draw", +1.5 …). A head start for the weaker team is never taken. If no
  favourite can be determined, no AH leg of that match qualifies.

No `min_edge_leg`, `min_odds` or `max_odds` rule. The engine stores the decision
in `value_legs.qualifies` (migration 0006); the dashboard reads it.

### 8.2 Favourite (`value/selection.py::favourite_side`)
From the blended `p_final` of the *home* selection on AH half lines of the same
snapshot batch:
- both −0.5 and +0.5 present: P(home win) = p(−0.5), P(away win) = 1 − p(+0.5);
  the larger is the favourite; an exact tie (±1e-9) → none;
- otherwise the fair line is the half line whose home probability is closest to
  0.5 (ties → the lower line): negative → home favourite, positive → away.

### 8.3 Slips under "likeliest"
Tie-break order (§3) already ranks by `p_final` first; unchanged. Differences:
- **daily_2odds:** no `min_slip_edge`. Before the 60-leg cap, the pool keeps legs
  priced in `[target_odds_min^(1/max_legs), target_odds_max]` (≈ 1.23–2.30),
  otherwise the cap fills with ~1.05 legs that can never reach 1.85.
- **mid_acca:** greedy by tie-break order, no slip-edge floor.
- **mega_acca:** pool = qualifying legs with `p_final ≥ min_leg_probability`; no
  `min_leg_edge`, no odds bounds.
- `slips.strategy` records the rule (`value` | `likeliest`); re-running picks only
  replaces pending/open slips of the same strategy. Performance views expose
  `strategy`; the dashboard's Performance page defaults to the active rule.

### 8.4 Tests (`tests/test_selection.py`, `tests/test_picks.py`)
- `favourite_side` table: ±0.5 home/away/tie, fair-line home/away, whole lines
  ignored, empty.
- `qualifies_likeliest` table: OU always, AH favourite yes / underdog no /
  unknown favourite no, any sanity reason no.
- Builders: daily ignores slip edge and the unreachable short prices; mid takes
  the likeliest legs with EM < 1; mega ignores odds bounds.
- Picks end-to-end: AH away (underdog) never qualifies, OU ok legs qualify,
  slips tagged `likeliest`; under `"value"` slips are tagged `value` and
  `qualifies` equals the §2.2 rule.

### 8.5 Not in scope yet
First/second-half handicaps and totals (user request 2026-09-30, planned next):
need SportyBet market discovery (docs/04 — never guess ids), a half-time
goals model (football-data HTHG/HTAG), and a backtest before entering slips.

### 8.6 Look-ahead horizon (user decision 2026-09-30)
`general.horizon_hours` (default 168 = 7 days) replaces the fixed 72 h of §1.2 and
docs/04 §2.2 for `make odds`, `make picks` and the dashboard fixtures page. Slip
windows are unchanged (daily 24 h, mid 72 h, mega = weekend window). Early odds move
more; re-run `make odds` before booking (booking refuses > 3% drift anyway).
