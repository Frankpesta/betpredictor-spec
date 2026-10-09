# 05 — Value Detection and Slip Building

Code: `engine/src/engine/value/` and `engine/src/engine/slips/`.
All functions pure; DB reads/writes happen in the `picks` job only.

---

## 1. Inputs to `make picks`

1. Latest successful `model_runs` per enabled league (fit if older than
   7 days or older than the newest finished match in `matches`; `make picks`
   refits automatically in that case and logs it). Since 2026-10-02 (user
   decision) `make daily` runs `ingest` before `odds -> picks -> book`, so new
   results reach `matches` and trigger this refit every day. A failed ingest
   is a warning on the daily run (its own `job_runs` row stays `failed`); the
   day continues on the stored data.
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

## 8. Selection strategy: "likeliest" (user decision 2026-09-30; superseded by §9 on 2026-10-08)

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

## 9. Selection strategy: "data_rule" (user decision 2026-10-08)

`value.selection = "data_rule"` replaces "likeliest" as the active rule. Why: under
"likeliest" the 30-leg mega acca of 2026-10-12 (ZY25KF) was 30 legs of Over 0.5 /
Under 5.5 at 1.02–1.08 — total odds 2.64, all-win chance 10.8%, expected multiplier
0.28. At 1.02 a leg must win 98% to break even; these were ~93%. Ranking by
probability alone always picks those markets. The user chose to move the agreed
data rule (2026-10-02, previously only in `data/adhoc/book_data_rule.py`) into the
engine, and to keep `mega_acca.max_legs = 30`.

### 9.1 Qualifying legs (`value/selection.py::qualifies_data_rule`)
Settings live in `[value.data_rule]`. Per match: P(score) per team from the model
score matrix (`1 − P(team scores 0)`); blanks = games the team failed to score in its
last `form_games` finished matches in the DB (any competition, before now). A leg
qualifies when **all** hold:
- `sanity_status = 'ok'` (§2.1 unchanged);
- `odds ≥ data_rule.min_odds` (1.20) and `p_final ≥ data_rule.min_leg_probability` (0.50);
- a data reason (`data_reason`):
  - **AH giving goals** (backed side's handicap < 0): that side is *strong* —
    P(score) ≥ `strong_score_p`, blanks ≤ `strong_max_blanks`, at least
    `min_form_games` games. Reason "to win" (−0.5) or "to win by N+" (−(N−0.5)).
  - **AH taking goals** (handicap > 0): the *other* side is *weak* — P(score) ≤
    `weak_score_p` and blanks ≥ `weak_min_blanks`. Reason "vs weak attack".
  - **Under:** at least one side is weak. Reason "weak attack".
  - **Over:** both sides P(score) ≥ `over_score_p` and blanks ≤ `over_max_blanks`.
    Reason "both score".

The reason (with the numbers) is stored in `value_legs.qualify_reason` (migration
0007) and shown per leg on slips and the fixture page. No edge rule; edge and
expected multiplier are still stored and shown (CLAUDE.md §6: this rule does not
claim value — the first run on 2026-10-08 still had negative edge on every leg).

### 9.2 Slips under "data_rule"
Builders treat it like "likeliest" (§8.3): eligibility = `qualifies`, no slip-edge
floor; daily keeps its reachable-price pool; tie-break order unchanged. The mega acca
takes up to `max_legs` qualifying legs, so its size is set by how many legs pass.

### 9.3 Tests
`tests/test_selection.py`: `p_scores`, `team_data`, `data_reason` table (give/take
goals, too few games, under/over, numbers in the text), `qualifies_data_rule` (odds
floor incl. 1.02, probability floor, sanity flags), builders use `qualifies` without
an edge floor. `tests/test_picks.py`: end to end — reason stored iff the leg
qualifies, odds/probability floors hold, slips tagged `data_rule`.

## 10. More markets: team goals, BTTS, 1X2, double chance (user request 2026-10-08)

SportyBet ids, outcomes and booking verified in
`docs/discovered/sportybet/2026-10-08/new-markets.md`. Migration 0008 widens the
`market`/`selection` CHECKs of `odds_snapshots` and `value_legs`.

| code | market | SportyBet id | selections | line |
|---|---|---|---|---|
| `OU_HOME` | home team goals | 19 | over / under | `total` (.5) |
| `OU_AWAY` | away team goals | 20 | over / under | `total` (.5) |
| `1X2` | match result | 1 | home / draw / away | none → 0.0 |
| `DC` | double chance | 10 | home_draw / home_away / draw_away | none → 0.0 |
| `BTTS` | both teams to score (GG/NG) | 29 | yes / no | none → 0.0 |

`value.markets` in settings lists the markets `make picks` prices (odds for every
market are stored regardless).

### 10.1 Pricing and settlement (`model/markets.py`)
The same score-matrix code prices and settles every market (docs/03 §8). Team goals use
the O/U margin on one team's goals (whole/quarter lines work as for OU but are
flagged). Lineless markets win or lose — no push: 1X2 by goal difference sign; double
chance home_draw = diff ≥ 0, home_away = diff ≠ 0, draw_away = diff ≤ 0; BTTS yes =
both ≥ 1. `is_binary(market, line)` (lineless or half line) replaces "half line" in the
expected-multiplier and calibration code.

### 10.2 Devig and sanity (`value/edge.py::evaluate_group`)
Multiplicative devig over all outcomes of the market: p_i = T·q_i / Σq, with T = 1
(two-/three-way) or **T = 2 for double chance** (each score wins two of three
selections). Overround = Σq / T, same 1.00–1.15 window. The line check is skipped for
lineless markets; every other §2.1 check applies. `evaluate_pair` (backtest, AH/OU) is
a wrapper.

### 10.3 Data rule (§9.1) for the new markets
- 1X2 home/away: as AH −0.5 (backed team strong, "to win"); the **draw never qualifies**.
- DC home_draw / draw_away: as AH +0.5 (the other team's attack is weak); **home_away
  never qualifies**.
- BTTS yes: as Over ("both score"); BTTS no: as Under ("weak attack").
- Team goals: Over needs *that* team strong ("to score", "to score N+"); Under needs that
  team weak.
- "likeliest" (§8) keeps to AH/OU; "value" (§2.2) applies unchanged.

### 10.4 Validation status
No historical SportyBet-comparable odds exist for team goals, BTTS or double chance
(football-data has 1X2 only), so these markets are **unvalidated** like INTL (docs/09):
labelled on every leg in the dashboard and judged on paper results. A 1X2 backtest is
possible later from football-data 1X2 odds.

### 10.5 Tests
`tests/test_more_markets.py` (settlement table, pricing identities incl. 1X2 home = AH
−0.5 and DC home_draw = AH +0.5, three-way and DC devig, lineless evaluation, data
reasons, likeliest exclusion), `tests/test_sportybet_markets.py` (ids/outcomes on the
2026-10-08 sample, short team labels, DC overround scaling).

### 10.6 Next: half-time markets
Ids recorded (1st/2nd half 1X2, DC, AH, O/U, team goals, GG/NG). Needs a half-time
goals model from football-data HTHG/HTAG, settlement from `gameScore[0]`/`[1]`, and a
backtest before entering slips.
