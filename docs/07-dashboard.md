# 07 — Local API and Next.js Dashboard

## 1. FastAPI engine API (`engine/src/engine/api/`)

Bound to `127.0.0.1:8765` only. No auth (local-only), but reject any request
whose `Host` header is not `127.0.0.1:8765` or `localhost:8765`, and allow
CORS only from `http://localhost:3000`.

Jobs run in a single background worker thread; only one job at a time
(a `threading.Lock`). Starting a job while one runs returns `409`.

| method | path | body | effect |
|---|---|---|---|
| GET | `/health` | — | `{ok, db_path, model_version, gate_passed}` |
| POST | `/jobs/{name}` | — | name ∈ `ingest, map-teams, fit, backtest, odds, picks, book, close, settle, daily`; returns `{job_run_id}` |
| GET | `/jobs/{job_run_id}` | — | status, summary, error |
| GET | `/jobs?limit=20` | — | recent job runs |
| PATCH | `/slips/{id}/mode` | `{mode: "paper"\|"placed", stake?: number, confirm_no_edge?: boolean}` | if `gate_passed = 0` and mode is `placed`, require `confirm_no_edge = true` else `400` |
| PATCH | `/slips/{id}/booking` | `{booking_code: string}` | manual code entry → `booking_status='manual'` |
| POST | `/slips/{id}/rebook` | — | sets `pending` and runs `book` for that slip |
| POST | `/aliases` | `{source, alias, team_id}` | approve an alias from the Data health page |

All request bodies validated with pydantic; all responses JSON. The CLI
`make` targets and these endpoints call the **same** job functions in
`engine/jobs.py`.

## 2. Dashboard (Next.js, `dashboard/`)

- App Router, TypeScript `strict`, Tailwind, shadcn/ui, Recharts.
- Reads the SQLite DB via Drizzle + `better-sqlite3` in **server
  components / route handlers only**, opened read-only
  (`{ readonly: true, fileMustExist: true }`). The DB path comes from
  `DB_PATH` in `dashboard/.env.local`, pointing to `../data/betpredictor.db`.
- Writes and job triggers go to the FastAPI engine
  (`NEXT_PUBLIC_ENGINE_URL=http://127.0.0.1:8765`).
- Times displayed in `Africa/Lagos`, e.g. "Sat 27 Sep, 15:00".
- Light/dark mode via shadcn theme. Mobile-friendly (the user may check on
  a phone on the same network — but the API stays on 127.0.0.1, so on mobile
  only read pages need to work; do not expose the API).

### 2.1 Global elements
- Top bar: last `odds` run time (red if > 3h old), current model version,
  gate status badge (green "Edge validated" / amber "Paper mode
  recommended"), a "Run daily" button (POST `/jobs/daily`) with live job
  status polling every 2s until done.
- Persistent amber banner when `gate_passed = 0` (copy from docs/03 §10.5).

### 2.2 Pages
1. **`/` Today**
   - Daily 2-odds card: legs (match, kickoff, market, line, selection, odds,
     model %, edge %), total odds, win probability, booking code with a copy
     button and booking status. If none: "No qualifying slip today — the
     model found no value."
   - Mid acca card and Mega acca card (same layout; mega has a "High risk"
     badge and shows win probability with 4 significant figures).
   - Buttons per slip: Copy code, Rebook, Enter code manually,
     Mark as placed (stake input; confirmation dialog when no validated edge).
2. **`/fixtures`** — table of upcoming matches (next 72h): league, kickoff,
   teams, λ, μ, best qualifying leg (if any), count of flagged legs. Filters:
   league, date, "qualifying only".
3. **`/fixtures/[id]`** — scoreline heatmap (11×11 from `score_matrix_json`,
   show up to 6×6 by default), table of every priced line (AH and OU) with
   model %, market devig %, final %, odds, edge, sanity status + reason.
4. **`/history`** — all slips, filter by type/status/mode/date; expandable
   legs with results and CLV.
5. **`/performance`** — from docs/06 §4: KPI cards (legs ROI, hit rate, mean
   CLV, slips ROI by type), equity curves, ROI by market / league / odds
   band, calibration chart (predicted vs observed with a y = x reference
   line and bin counts), expected vs actual profit per slip type. Toggle:
   all / paper / placed.
6. **`/model`** — latest model run per league (fit date, n matches,
   converged, home advantage, ρ, team ratings table sorted by att − def),
   latest backtest summary (holdout metrics, gate result, link to report
   file path), button to run backtest.
7. **`/data-health`** — recent `job_runs` with summaries and errors;
   unresolved team names with a dropdown to approve an alias (POST
   `/aliases`); matches in `needs_review`; failed bookings; stale data
   warnings.

### 2.3 UI rules
- Never use the words "sure", "guaranteed", "banker", "fixed" anywhere.
- Percentages to 1 decimal place except the mega acca probability.
- Odds to 2 decimal places.
- Every number that comes from the model shows the model version on hover.
- Empty states are explicit and explain what to run.

## 3. Dashboard checks
- `pnpm typecheck` and `pnpm lint` pass with zero errors.
- Every page renders against an empty DB (fresh migrate) without throwing.
- Every page renders against a seeded fixture DB created by
  `engine/tests/fixtures/seed_demo.py` (synthetic data, clearly marked demo).
