# 08 — Build Phases, Tasks and Acceptance Checks

Work strictly in order. At the end of each phase: run every acceptance
check, paste outputs to the user, and **wait for approval** before the next
phase. If a check fails and the fix would change anything in `docs/`
(other than `docs/discovered/`), stop and ask.

---

## Phase 0 — Scaffold
Tasks:
1. Create the repo layout from CLAUDE.md §3, `.gitignore` (`data/`, `.env`,
   `node_modules`, `.venv`, `__pycache__`), `README.md`, `Makefile`.
2. `engine/pyproject.toml` with the fixed stack; `uv sync` works.
3. `config/settings.toml` exactly as docs/01 §4; `config.py` loads and
   validates it with pydantic (unknown keys → error).
4. Structured logging (JSON lines to `data/logs/engine.log` + readable console).
5. Alembic set up; first migration creates **all** tables from docs/01 §3
   with constraints and indexes on every FK and on
   `matches(kickoff_utc)`, `odds_snapshots(match_id, captured_at)`.
6. `jobs.py` with the `job_runs` context manager (start/finish/fail, summary).
7. Next.js app created in `dashboard/` with Tailwind + shadcn/ui; Drizzle
   configured read-only; `drizzle-kit pull` generates the schema.

Acceptance:
- `make setup && make migrate && make test` succeed on a clean clone.
- `sqlite3 data/betpredictor.db ".tables"` lists every table.
- Dashboard dev server renders an empty "Today" page.

## Phase 1 — Data pipeline (docs/02)
Tasks:
1. football-data discovery → `docs/discovered/football-data.md`.
2. `ingest/football_data.py` + tests on saved sample CSVs (offline).
3. Understat discovery → `docs/discovered/understat.md`; `ingest/understat.py`.
4. `mapping/teams.py`, `aliases_seed.toml`, `make map-teams`, tricky-pair tests.
5. `make ingest` end to end.

Acceptance: every item in docs/02 §4, plus `make ingest` run twice gives
identical row counts (idempotency).

## Phase 2 — Model and backtest (docs/03)  ← make-or-break
Tasks:
1. `settlement` functions + the full docs/03 §8.6 test table.
2. `dixon_coles.py`, `xg_blend.py`, `score_matrix.py`, `markets.py` +
   property tests §8.7 + recovery test §8.8 (+ gradient check if analytic).
3. `calibration.py` + tests.
4. `make fit` for enabled leagues → `model_runs`; print per league: n
   matches, converged, h, ρ, top 5 and bottom 5 teams by `att − def`.
5. `backtest/` walk-forward with leakage assertions, tuning grid, holdout,
   metrics, markdown report, `backtest_runs` row.
6. Write chosen parameters back to `config/settings.toml` **only after
   showing the user** the tuning table and getting approval.

Acceptance:
- All model/settlement tests pass.
- Team rankings look football-sensible (user eyeballs them).
- Backtest report produced; gate result stated plainly (pass or fail),
  with holdout ROI, bets, ECE, mean CLV, and baseline log losses.
- If gate fails: tell the user clearly, continue building, banner will show.

## Phase 3 — SportyBet odds (docs/04 §1–2)
Tasks:
1. `make discover` headed session with the user; fill F1–F12 in
   `docs/discovered/sportybet/<date>/endpoints.md`; save samples.
2. F9 handicap-sign check recorded.
3. `sportybet/client.py` (both transports), `markets.py` parser + offline
   tests on samples.
4. Map SportyBet team names (`make map-teams`), approve aliases with user.
5. `make odds` end to end.

Acceptance:
- Upcoming events for all 5 leagues stored with `sportybet_event_id`.
- 0 unresolved names for the fetched events (or listed and approved).
- For 3 random events, print the stored AH and OU lines/odds next to a
  screenshot of the event page; user confirms they match (including AH sign).
- All overrounds within [1.00, 1.15].

## Phase 4 — Value engine and slips (docs/05)
Tasks:
1. `value/edge.py`, `value/sanity.py` + tests §2.3.
2. `slips/builder.py`, `slips/constraints.py` + tests §7 (including the
   worked example).
3. `make picks` end to end, writes `value_legs` + `slips`.

Acceptance:
- Job summary shows: legs priced, flagged by reason, qualifying count,
  slips created per type (or "none — no value").
- Running `make picks` twice produces identical slips.

## Phase 5 — Booking codes (docs/04 §3)
Tasks:
1. Stable selectors discovered with user; `selectors.py`.
2. `booking.py` (endpoint path if F11 allows, UI path always available),
   odds-drift rule, screenshots, failure handling.
3. `make book`.

Acceptance:
- A 2-leg test slip is booked; the user loads the code on SportyBet and
  confirms the exact legs, lines and selections appear.
- A deliberately drifted leg (tests simulate odds change) → `failed` with
  reason, no booking attempted.

## Phase 6 — Settlement and tracking (docs/06)
Tasks: `make close`, `make settle`, SQL views migration, tests.

Acceptance: settlement tests pass; running settle on a slip whose
matches finished produces correct statuses; views return rows.

## Phase 7 — API and dashboard (docs/07)
Tasks: FastAPI routes; all 7 pages; demo seed script; empty-state handling.

Acceptance: docs/07 §3 checks; the user can run the full daily flow from
the dashboard: Run daily → see slips → copy code → mark placed.

## Phase 8 — Paper trading period (no code)
- Use `make daily` (and `make close`, `make settle`) for **at least 4–6
  weeks** with all slips in paper mode.
- Review `/performance` weekly: leg ROI, mean CLV, calibration, expected vs
  actual. Real money only if paper results agree with the backtest and CLV
  is positive. This is the user's decision; the tool only reports.
- **Restarted 2026-09-30** when the user switched selection to "likeliest"
  (docs/05 §8). Judge the likeliest period (from 2026-09-30) on its own:
  `/performance` defaults to the active strategy. The backtest gate measured
  the value rule, so it does not validate "likeliest"; for it, compare hit rate
  with the average `p_final` and watch the expected multiplier (< 1 means the
  rule loses money over time even when most slips win).

---

## Definition of done (whole project)
- `make test` green; ruff clean; mypy strict clean on `model/` and `value/`.
- Every discovered external fact documented in `docs/discovered/`.
- No code path can log in to SportyBet or place a bet.
- `README.md` explains setup and the daily routine:
  `make daily` (morning / Friday for the weekend) → check dashboard →
  `make close` before main kickoffs → `make settle` next day.
