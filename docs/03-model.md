# 03 — The Model (Dixon-Coles + xG blend) and Market Maths

Everything here lives in `engine/src/engine/model/` and must be pure
functions (numpy in, numpy/dataclasses out, no DB or network access).

---

## 1. Notation

For a match between home team *i* and away team *j*:

- `λ` = expected home goals, `μ` = expected away goals
- `att[k]`, `def[k]` = attack and defence parameters of team *k*
  (a **higher `def` means a worse defence**, i.e. concedes more)
- `γ` = intercept (log baseline scoring rate), `h` = home advantage
- `ρ` = Dixon-Coles low-score dependence parameter

```
log λ = γ + h + att[i] + def[j]
log μ = γ     + att[j] + def[i]
```

Identifiability constraints: `Σ att = 0` and `Σ def = 0`. Implement by
optimising n−1 free values for each and setting the last to minus the sum.

One model is fitted **per league**. Never mix leagues in one fit.

## 2. Dixon-Coles correction τ

```
τ(0,0) = 1 − λ·μ·ρ
τ(0,1) = 1 + λ·ρ
τ(1,0) = 1 + μ·ρ
τ(1,1) = 1 − ρ
τ(x,y) = 1   otherwise
```

## 3. Goals model — weighted log-likelihood

For training matches k with goals (x_k, y_k), days before fit date t_k:

```
w_k = exp(−xi_per_day · t_k)
LL  = Σ_k w_k · [ log τ(x_k, y_k; λ_k, μ_k, ρ) + x_k·log λ_k − λ_k + y_k·log μ_k − μ_k ]
objective = −LL + ridge_lambda · (Σ att² + Σ def²)
```

(The `−log x! − log y!` terms are constants and are dropped.)

- Training window: all finished matches of this league in the last
  `train_seasons_back` seasons **strictly before** the fit date.
- If any `τ ≤ 0` for the current parameters, return objective `1e12`
  (guards the optimiser).
- Optimiser: `scipy.optimize.minimize(method="L-BFGS-B")`, bounds only on ρ
  (`rho_bounds`). Initial values: `γ = log(mean goals per team per match)`,
  `h = 0.25`, `att = def = 0`, `ρ = 0`. Vectorise the objective with numpy
  (no Python loop over matches). An analytic gradient is welcome but must be
  checked against `scipy.optimize.check_grad` in a test (error < 1e-4).
- If `result.success` is False: retry once with `h = 0.1, ρ = −0.05`;
  if still False, fail the job with a clear error. Never silently use an
  unconverged fit.

## 4. xG model

Identical structure but fitted to **xG values** as the "goals":

```
LL_xg = Σ_k w_k · [ xg_h·log λ_k − λ_k + xg_a·log μ_k − μ_k ]      (ρ fixed at 0, no τ)
```

(This is a Poisson quasi-likelihood; non-integer targets are fine.)
Only for leagues and matches with xG. Same ridge, same decay.

## 5. Blending

For a fixture, compute `λ_g, μ_g` from the goals model and `λ_x, μ_x` from
the xG model, then:

```
log λ = w·log λ_g + (1−w)·log λ_x
log μ = w·log μ_g + (1−w)·log μ_x          with w = xg_blend_weight
```

If the league has no xG model, `λ = λ_g, μ = μ_g`. The score matrix always
uses **ρ from the goals model**.

## 6. Score matrix

```
P[x][y] = τ(x,y; λ, μ, ρ) · Poisson(x; λ) · Poisson(y; μ),   x, y ∈ 0..max_goals
```

Before building P, compute the truncated tail mass
`tail = 1 − poisson.cdf(max_goals, λ) · poisson.cdf(max_goals, μ)`.
If `tail > 1e-4`, raise — λ or μ is absurd and something upstream is wrong.
Otherwise build P as above and normalise `P /= ΣP`. Store as `(max_goals+1)²` nested list.

## 7. Low-confidence teams

A team with fewer than `min_team_matches` training matches **in this
league within the window** (typically newly promoted teams early in the
season) is flagged `low_confidence`. Predictions are still stored, but legs
involving such teams are **excluded from slips** (see docs/05).

---

## 8. Market settlement maths (single source of truth)

These functions are used both to price markets from the score matrix **and**
to settle real bets later. There must be exactly one implementation.

### 8.1 Result types and multipliers

| result | return per unit stake at decimal odds `o` |
|---|---|
| `win` | `o` |
| `half_win` | `(1 + o) / 2` |
| `push` | `1` |
| `half_loss` | `0.5` |
| `loss` | `0` |

### 8.2 Line classification
Let `f = line mod 1` (handle negatives correctly using `abs`).
- `f ∈ {0.5}` → half line
- `f ∈ {0.0}` → whole line (push possible)
- `f ∈ {0.25, 0.75}` → quarter line: split into `line − 0.25` and
  `line + 0.25`, half stake on each.
- Anything else → raise `ValueError`.

### 8.3 Settling a single (non-quarter) line
Compute margin `m`:
- Over `L`: `m = total − L`
- Under `L`: `m = L − total`
- AH home with home-perspective line `H`: `m = (home_goals − away_goals) + H`
- AH away with home-perspective line `H`: `m = (away_goals − home_goals) − H`

`m > 0 → win`, `m = 0 → push`, `m < 0 → loss`. Compare with a tolerance of
1e-9, never exact float equality.

### 8.4 Combining the two halves of a quarter line
| half A | half B | result |
|---|---|---|
| win | win | win |
| win | push | half_win |
| push | loss | half_loss |
| loss | loss | loss |
| push | push | push (cannot occur for quarter lines; raise if it does) |
| win | loss | impossible for adjacent lines; raise |

(Order of A/B does not matter.)

### 8.5 Pricing a selection from the score matrix
For each cell (x, y), settle the selection, then sum cell probabilities per
result type → `{p_win, p_half_win, p_push, p_half_loss, p_loss}` (sums to 1).

```
expected_multiplier(o) = p_win·o + p_half_win·(1+o)/2 + p_push·1 + p_half_loss·0.5
edge = expected_multiplier − 1
```

### 8.6 Mandatory test table (`tests/test_settlement.py`)
AH (line is home perspective):

| selection | line H | score | expected |
|---|---|---|---|
| home | −0.5 | 1-0 | win |
| home | −0.5 | 0-0 | loss |
| home | −0.5 | 1-1 | loss |
| home | +0.5 | 0-0 | win |
| home | +0.5 | 0-1 | loss |
| home | −1.0 | 2-0 | win |
| home | −1.0 | 1-0 | push |
| home | −1.0 | 0-0 | loss |
| home | −0.25 | 1-0 | win |
| home | −0.25 | 0-0 | half_loss |
| home | −0.25 | 0-1 | loss |
| home | +0.25 | 0-0 | half_win |
| home | +0.25 | 1-0 | win |
| home | +0.25 | 0-1 | loss |
| home | −0.75 | 2-0 | win |
| home | −0.75 | 1-0 | half_win |
| home | −0.75 | 0-0 | loss |
| home | +0.75 | 0-0 | win |
| home | +0.75 | 0-1 | half_loss |
| home | +0.75 | 0-2 | loss |
| home | −1.5 | 2-0 | win |
| home | −1.5 | 1-0 | loss |
| away | −0.5 | 0-0 | win |
| away | −0.5 | 1-0 | loss |
| away | −0.75 | 0-0 | win |
| away | −0.75 | 1-0 | half_loss |
| away | −0.75 | 2-0 | loss |

Over/Under (total goals):

| selection | line | total | expected |
|---|---|---|---|
| over | 2.5 | 3 | win |
| over | 2.5 | 2 | loss |
| under | 2.5 | 2 | win |
| under | 2.5 | 3 | loss |
| over | 2.0 | 3 | win |
| over | 2.0 | 2 | push |
| over | 2.0 | 1 | loss |
| over | 2.25 | 3 | win |
| over | 2.25 | 2 | half_loss |
| over | 2.75 | 4 | win |
| over | 2.75 | 3 | half_win |
| over | 2.75 | 2 | loss |
| under | 2.25 | 1 | win |
| under | 2.25 | 2 | half_win |
| under | 2.25 | 3 | loss |
| under | 2.75 | 2 | win |
| under | 2.75 | 3 | half_loss |

Multiplier tests at `o = 1.90`: win 1.90, half_win 1.45, push 1.0,
half_loss 0.5, loss 0.

### 8.7 Property tests (`tests/test_model_properties.py`)
- Score matrix sums to 1 (±1e-9), all entries ≥ 0.
- With ρ = 0 the matrix equals the outer product of two Poisson PMFs
  (after identical truncation/normalisation).
- For every half line: `P(over) + P(under) = 1`; `P(AH home) + P(AH away) = 1`;
  and `p_push = p_half_win = p_half_loss = 0`.
- For every priced selection the five probabilities sum to 1 (±1e-9).
- `P(over 2.5)` is non-decreasing in λ with μ fixed.

### 8.8 Parameter recovery test (`tests/test_dixon_coles_recovery.py`)
Simulate a 20-team league with known `att, def` (drawn from N(0, 0.25²),
then centred), `γ = 0.15`, `h = 0.25`, `ρ = −0.08`; three double
round-robins; sample each scoreline directly from its DC score matrix
(seeded RNG). Fit with `xi_per_day = 0`, `ridge_lambda = 0`. Assert:
- Pearson correlation of fitted vs true `att` > 0.9, same for `def`;
- `|h_fit − 0.25| < 0.08`; `|ρ_fit − (−0.08)| < 0.07`.

---

## 9. Calibration

`calibration.py` provides:
- `ece(probs, outcomes, bins=10)` — expected calibration error with equal-
  width bins (weighted by bin count);
- `reliability_table(...)` — per-bin mean predicted vs observed rate, count
  (feeds the dashboard calibration chart).

For half lines, outcome = 1 if `win`, else 0.

---

## 10. Walk-forward backtest (`backtest/`)

### 10.1 No leakage — enforced by assertion
For every refit date `d`: training matches have `kickoff_utc < d`;
predicted matches have `d ≤ kickoff_utc < d + refit_every_days`. Assert this
in code for every block (not only in tests).

### 10.2 Procedure
1. For each enabled league and each season in `backtest.test_seasons`, step
   through the season in blocks of `refit_every_days`.
2. At each block start, fit goals + xG models on data before the block.
3. Price every match in the block for: O/U 2.5 (both sides) and AH at the
   `AHh` line (both sides). Use `odds_source` opening odds as the "bet price"
   and `closing_source` closing odds for CLV.
4. Apply the exact same value pipeline as live (docs/05 §1–3: devig, shrink,
   sanity, edge, odds bounds, half-lines only, low-confidence exclusion).
5. Settle with the §8 functions. Flat stake 1 unit per qualifying leg.

### 10.3 Tuning vs holdout (prevents overfitting)
- **Tuning seasons** = all `test_seasons` except the last.
  Grid (use `refit_every_days = 14` during the grid to save time; parallelise
  fits with `ProcessPoolExecutor` — this is local CPU, not scraping):
  - `xi_per_day ∈ {0.0010, 0.0015, 0.0019, 0.0025, 0.0035}`
  - `xg_blend_weight ∈ {0.3, 0.5, 0.7, 1.0}`
  - `market_shrink_weight ∈ {0.3, 0.5, 0.7, 1.0}`
  - `min_edge_leg ∈ {0.02, 0.03, 0.05}`
  Choose `xi` and `xg_blend_weight` by **lowest log loss on O/U 2.5**
  (a proper scoring rule, not ROI). Then choose `market_shrink_weight` by
  lowest log loss, and `min_edge_leg` by ROI **only among settings with
  ≥ min_bets_for_roi bets**.
- **Holdout season** = last `test_seasons` entry, run **once** with the chosen
  parameters at `refit_every_days = 7`. Report holdout numbers separately and
  prominently. Never re-tune after looking at holdout results without telling
  the user that the holdout is no longer clean.

### 10.4 Metrics (stored in `backtest_runs.metrics_json`)
Per league and overall, per market (OU, AH):
- log loss and Brier of `p_final` (half lines);
- log loss of devigged `odds_source` and of devigged `closing_source` (baselines);
- ECE + reliability table;
- qualifying bets count, hit rate, flat-stake ROI, profit in units,
  max drawdown in units, longest losing streak;
- mean CLV = mean(odds_taken / closing_odds − 1) on qualifying bets;
- simulated daily-2-odds slips over the holdout (using docs/05 §4 on each
  day's qualifying legs): number of slips, slip hit rate, ROI.

### 10.5 Gate
The model **passes** only if, on the holdout: overall ECE ≤
`gates.max_calibration_ece`, qualifying bets ≥ `gates.min_bets_for_roi`,
and ROI > `gates.min_roi`. Mean CLV > 0 is reported as supporting evidence.

A failing gate does **not** stop the build. It sets `gate_passed = 0`, the
dashboard shows a persistent banner "Model has not demonstrated an edge —
paper mode recommended", and switching a slip to `placed` asks for explicit
confirmation. Write the full markdown report to `data/reports/` and show
the user the summary table.
