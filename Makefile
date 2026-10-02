# BetPredictor task runner. Recipes are written to work under both sh and cmd.exe.
ENGINE = cd engine && uv run

.PHONY: setup migrate ingest map-teams fit backtest discover odds picks book close settle daily api dashboard dev test seed-demo dashboard-check

setup:
	cd engine && uv sync
	cd engine && uv run playwright install chromium
	cd dashboard && pnpm install
	$(MAKE) migrate

migrate:
	$(ENGINE) alembic upgrade head

ingest:
	$(ENGINE) engine ingest

map-teams:
	$(ENGINE) engine map-teams

fit:
	$(ENGINE) engine fit

backtest:
	$(ENGINE) engine backtest

discover:
	$(ENGINE) engine discover

odds:
	$(ENGINE) engine odds

picks:
	$(ENGINE) engine picks

book:
	$(ENGINE) engine book

close:
	$(ENGINE) engine close

settle:
	$(ENGINE) engine settle

# ingest -> odds -> picks -> book (the normal daily command; a failed ingest is warned, not fatal)
daily:
	$(ENGINE) engine daily

api:
	$(ENGINE) python -m engine.api.main

dashboard:
	cd dashboard && pnpm dev

dev:
	$(MAKE) -j2 api dashboard

# Synthetic demo DB (clearly marked DEMO) for trying the dashboard: DB_PATH=../data/tmp/demo.db
seed-demo:
	$(ENGINE) python tests/fixtures/seed_demo.py --fresh ../data/tmp/demo.db

# docs/07 §3: every page renders against an empty DB and the demo DB (never touches the real DB)
dashboard-check:
	$(ENGINE) python tests/fixtures/seed_demo.py --fresh --empty ../data/tmp/empty.db
	$(ENGINE) python tests/fixtures/seed_demo.py --fresh ../data/tmp/demo.db
	cd dashboard && pnpm build
	cd dashboard && node scripts/smoke.mjs ../data/tmp/empty.db ../data/tmp/demo.db

test:
	$(ENGINE) ruff check .
	$(ENGINE) ruff format --check .
	$(ENGINE) mypy src/engine
	$(ENGINE) pytest
	cd dashboard && pnpm typecheck
	cd dashboard && pnpm lint --max-warnings=0
