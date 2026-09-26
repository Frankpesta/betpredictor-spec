# 01 — Architecture, Database, Configuration

## 1. Data flow

```
football-data.co.uk CSVs ──┐
Understat xG ──────────────┼─► ingest ─► matches / team_xg ─► model fit ─► model_runs
                           │                                   │
SportyBet (fixtures+odds) ─┴─► sportybet client ─► events, odds_snapshots
                                                               │
                  predictions (λ, μ, score matrix) ◄───────────┘
                               │
                   value legs (model p vs SportyBet odds, sanity checks)
                               │
                   slip builder ─► slips + slip_legs ─► booking codes (Playwright)
                               │
                   results ─► settlement ─► performance metrics + CLV
                               │
                   FastAPI (127.0.0.1:8765) ◄──► Next.js dashboard (localhost:3000)
```

All processes run on the user's machine, started manually (`make daily`, or
the dashboard's "Run" buttons which call FastAPI, which runs the same job
functions in a background thread — one job at a time, guarded by a lock).

## 2. Enabled competitions (v1)

| key | name | football-data code | Understat league | xG available |
|---|---|---|---|---|
| EPL | Premier League | E0 | EPL | yes |
| LALIGA | La Liga | SP1 | La_liga | yes |
| SERIEA | Serie A | I1 | Serie_A | yes |
| BUNDES | Bundesliga | D1 | Bundesliga | yes |
| LIGUE1 | Ligue 1 | F1 | Ligue_1 | yes |
| CHAMP | Championship | E1 | — | no (goals-only model) |
| ERED | Eredivisie | N1 | — | no (goals-only model) |

Only EPL, LALIGA, SERIEA, BUNDES, LIGUE1 are `enabled = true` at first.
CHAMP and ERED are defined but disabled. **UEFA Champions League is out of
scope for v1** (no single-league rating system; cross-league strength needs a
separate model). Do not add it.

## 3. Database schema (SQLite, via Alembic)

Conventions: integer primary keys `id`; `created_at` / `updated_at` UTC
timestamps on every table; all enums stored as TEXT with a CHECK constraint;
floats as REAL; JSON as TEXT validated by pydantic on write.

Enable at connection time: `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;`

### leagues
| col | type | notes |
|---|---|---|
| key | TEXT UNIQUE | e.g. `EPL` |
| name | TEXT | |
| fd_code | TEXT | football-data code |
| understat_key | TEXT NULL | |
| enabled | INTEGER | 0/1, mirrors settings |

### teams
| col | type | notes |
|---|---|---|
| league_id | FK leagues | current league |
| canonical_name | TEXT | the football-data.co.uk spelling is canonical |
| UNIQUE(canonical_name) | | |

### team_aliases
| col | type | notes |
|---|---|---|
| team_id | FK teams | |
| source | TEXT CHECK in ('football_data','understat','sportybet') | |
| alias | TEXT | exact string as it appears in that source |
| UNIQUE(source, alias) | | |

### matches  (historical + upcoming, one row per real fixture)
| col | type | notes |
|---|---|---|
| league_id | FK | |
| season | TEXT | `2024-25` |
| kickoff_utc | TIMESTAMP | |
| home_team_id / away_team_id | FK teams | |
| home_goals / away_goals | INTEGER NULL | NULL until played |
| home_xg / away_xg | REAL NULL | from Understat |
| status | TEXT CHECK in ('scheduled','finished','postponed','cancelled','abandoned','needs_review') | |
| fd_row_hash | TEXT NULL | idempotent re-ingest |
| sportybet_event_id | TEXT NULL UNIQUE | e.g. `sr:match:12345678` |
| UNIQUE(league_id, home_team_id, away_team_id, season) | | a pair meets once at home per season |

### historical_odds  (from football-data.co.uk, for backtesting only)
| col | type | notes |
|---|---|---|
| match_id | FK | |
| bookmaker | TEXT | `B365`, `PS` (Pinnacle), `AVG`, `MAX` |
| timing | TEXT CHECK in ('open','close') | |
| market | TEXT CHECK in ('OU','AH','1X2') | |
| line | REAL NULL | 2.5 for OU; home handicap for AH |
| selection | TEXT CHECK in ('home','draw','away','over','under') | |
| odds | REAL | decimal |

### model_runs
| col | type | notes |
|---|---|---|
| league_id | FK | |
| model_version | TEXT | semantic, e.g. `dc-1.0.0` |
| fitted_at | TIMESTAMP | |
| train_from / train_to | DATE | |
| params_json | TEXT | attack/defence per team, home_adv, rho, xi, blend weight |
| converged | INTEGER | from optimizer |
| neg_log_lik | REAL | |
| n_matches | INTEGER | |

### predictions
| col | type | notes |
|---|---|---|
| match_id | FK | |
| model_run_id | FK | |
| lambda_home / lambda_away | REAL | expected goals |
| score_matrix_json | TEXT | 11×11 list of lists, sums to 1 |
| created_at | | |
| UNIQUE(match_id, model_run_id) | | |

### odds_snapshots  (SportyBet live prices)
| col | type | notes |
|---|---|---|
| match_id | FK | |
| captured_at | TIMESTAMP | |
| snapshot_kind | TEXT CHECK in ('pick','close') | `close` = near-kickoff snapshot for CLV |
| market | TEXT CHECK in ('OU','AH') | |
| line | REAL | O/U total line, or AH line **from the home team's perspective** |
| selection | TEXT CHECK in ('over','under','home','away') | |
| odds | REAL | |
| sb_market_id | TEXT | raw SportyBet market id |
| sb_specifier | TEXT | raw specifier string, e.g. `hcp=-0.5` / `total=2.5` |
| sb_outcome_id | TEXT | raw outcome id |
| is_active | INTEGER | market open/suspended |

### value_legs  (candidate selections produced by `make picks`)
| col | type | notes |
|---|---|---|
| match_id, prediction_id, odds_snapshot_id | FKs | |
| market, line, selection | | copied for convenience |
| odds | REAL | |
| p_model | REAL | raw model probability of full win (see docs/05) |
| p_final | REAL | after market shrinkage |
| p_market_devig | REAL | |
| p_win, p_half_win, p_push, p_half_loss, p_loss | REAL | outcome distribution (sums to 1) |
| expected_multiplier | REAL | E[return per unit stake] |
| edge | REAL | expected_multiplier − 1 |
| sanity_status | TEXT CHECK in ('ok','flagged') | |
| sanity_reason | TEXT NULL | |
| created_at | | |

### slips
| col | type | notes |
|---|---|---|
| slip_type | TEXT CHECK in ('daily_2odds','mid_acca','mega_acca') | |
| slip_date | DATE | Lagos date it is for |
| window_start_utc / window_end_utc | TIMESTAMP | fixture window used |
| total_odds | REAL | product of leg odds |
| p_all_win | REAL | product of leg p_win (half-lines only in v1, so = no-loss) |
| expected_multiplier | REAL | product of leg expected multipliers |
| booking_code | TEXT NULL | |
| booking_status | TEXT CHECK in ('pending','booked','failed','manual') | |
| booking_error | TEXT NULL | |
| mode | TEXT CHECK in ('paper','placed') DEFAULT 'paper' | |
| stake | REAL NULL | user-entered, only for `placed` |
| status | TEXT CHECK in ('open','won','lost','void','partial') | |
| return_multiplier | REAL NULL | realised return per unit stake |
| model_version | TEXT | |

### slip_legs
| col | type | notes |
|---|---|---|
| slip_id | FK | |
| value_leg_id | FK | |
| leg_order | INTEGER | |
| result | TEXT CHECK in ('pending','win','half_win','push','half_loss','loss','void') | |
| result_multiplier | REAL NULL | 0, 0.5, 1, (1+o)/2, o |
| closing_odds | REAL NULL | from `close` snapshot |
| clv | REAL NULL | odds_taken / closing_odds − 1 |

### backtest_runs
| col | type | notes |
|---|---|---|
| started_at, finished_at | | |
| config_json | TEXT | full config snapshot |
| metrics_json | TEXT | see docs/03 §8 |
| gate_passed | INTEGER | |
| report_path | TEXT | markdown report under `data/reports/` |

### job_runs
| col | type | notes |
|---|---|---|
| job_name | TEXT | `ingest`, `odds`, `picks`, ... |
| started_at, finished_at | | |
| status | TEXT CHECK in ('running','success','failed') | |
| summary_json | TEXT | counts: fetched, inserted, updated, skipped (+reasons) |
| error | TEXT NULL | traceback on failure |

### unresolved_names
| col | type | notes |
|---|---|---|
| source | TEXT | |
| raw_name | TEXT | |
| league_key | TEXT NULL | |
| first_seen / last_seen | TIMESTAMP | |
| UNIQUE(source, raw_name) | | |

## 4. `config/settings.toml` (create with exactly these keys and defaults)

```toml
[general]
timezone = "Africa/Lagos"
db_path = "data/betpredictor.db"
raw_dir = "data/raw"
reports_dir = "data/reports"

[leagues]
enabled = ["EPL", "LALIGA", "SERIEA", "BUNDES", "LIGUE1"]
history_seasons = ["2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26","2026-27"]

[scraping]
min_delay_seconds = 4.0
max_delay_seconds = 9.0
max_retries = 3
user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
sportybet_country = "ng"
headless = true

[model]
version = "dc-1.0.0"
train_seasons_back = 3          # seasons of history used per fit
xi_per_day = 0.0019             # time-decay; tuned by backtest
ridge_lambda = 0.5              # L2 penalty on attack/defence params
max_goals = 10                  # score matrix is (max_goals+1)^2
xg_blend_weight = 0.5           # weight on goals-model log-rates; 1-w on xG-model
rho_bounds = [-0.2, 0.2]
min_team_matches = 8            # below this, team is flagged low-confidence

[value]
market_shrink_weight = 0.7      # p_final = w*p_model + (1-w)*p_market_devig ; tuned by backtest
min_edge_leg = 0.03
max_model_market_gap = 0.15     # abs(p_final - p_market_devig) above this => flagged
min_odds = 1.20
max_odds = 2.60
allowed_lines = "half_only"     # v1: only x.5 lines enter slips

[slips.daily_2odds]
enabled = true
min_legs = 2
max_legs = 3
target_odds_min = 1.85
target_odds_max = 2.30
min_leg_probability = 0.60
min_slip_edge = 0.02
window_hours = 24

[slips.mid_acca]
enabled = true
min_legs = 5
max_legs = 8
min_leg_probability = 0.62
min_slip_edge = 0.00

[slips.mega_acca]
enabled = true
max_legs = 30
min_leg_probability = 0.65
min_leg_edge = 0.00
weekend_start = "Fri 18:00"     # Lagos time
weekend_end = "Mon 02:00"

[backtest]
test_seasons = ["2023-24","2024-25","2025-26"]
refit_every_days = 7
odds_source = "B365"            # bet price
closing_source = "PS"           # CLV benchmark (Pinnacle closing)

[gates]
max_calibration_ece = 0.03
min_bets_for_roi = 400
min_roi = 0.0                   # must be > this
require_beats_market_logloss = false

[api]
host = "127.0.0.1"
port = 8765
```

## 5. Idempotency rules

- Re-running any ingest must not duplicate rows (upsert on natural keys).
- Re-running `make picks` for the same Lagos date **replaces** that date's
  `open`, `booking_status='pending'` slips; it must never touch booked or
  settled slips.
- Re-running `make book` only processes slips with `booking_status` in
  (`pending`,`failed`).
