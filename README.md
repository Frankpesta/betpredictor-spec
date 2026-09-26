# BetPredictor

Personal, local-only football value-betting engine: Dixon-Coles + xG model for
Asian Handicap and Over/Under, compared with SportyBet (Nigeria) odds, building
daily slips and SportyBet booking codes, then tracking results honestly.

It **never places bets** and never logs in: it only produces booking codes.
Slips are `paper` by default. No model can guarantee wins; the backtest gate and
the paper-trading period exist to measure whether any edge is real.

The specification lives in `CLAUDE.md` and `docs/` (read `docs/00-START-HERE.md`).

## Requirements

- Python 3.12 via [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and pnpm
- GNU make, sqlite3 CLI (optional, for inspection)

On Windows: `winget install astral-sh.uv ezwinports.make SQLite.SQLite`.

## Setup

```sh
cp .env.example .env            # optional overrides of config/settings.toml
make setup                      # uv sync, playwright chromium, pnpm install, migrate
make test                       # ruff, mypy, pytest, dashboard typecheck + lint
```

All tunables live in `config/settings.toml`. Data, logs and reports go under `data/`
(gitignored). The database is `data/betpredictor.db` (SQLite, WAL).

## Daily routine

```sh
make daily        # morning (Friday for the weekend): odds -> picks -> book
make dev          # API on 127.0.0.1:8765 + dashboard on http://localhost:3000
make close        # ~30-60 min before the main kickoffs (closing odds for CLV)
make settle       # next day: fetch results, settle legs and slips
```

## Other commands

| command | what it does |
|---|---|
| `make migrate` | apply Alembic migrations (the only thing that changes the schema) |
| `make ingest` | football-data.co.uk history + Understat xG |
| `make map-teams` | resolve team names; print unresolved + proposals |
| `make fit` | fit models for enabled leagues |
| `make backtest` | walk-forward backtest + gate report |
| `make discover` | SportyBet discovery (headed browser, interactive) |
| `make odds` / `picks` / `book` | individual steps of `make daily` |
| `make api` / `make dashboard` | run one half of `make dev` |

After a schema migration, regenerate the dashboard's DB types with
`cd dashboard && pnpm db:pull`.
