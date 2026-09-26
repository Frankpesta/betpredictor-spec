# BetPredictor task runner. Recipes are written to work under both sh and cmd.exe.
ENGINE = cd engine && uv run

.PHONY: setup migrate ingest map-teams fit backtest discover odds picks book close settle daily api dashboard dev test

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

# odds -> picks -> book (the normal daily command)
daily:
	$(ENGINE) engine daily

api:
	$(ENGINE) python -m engine.api.main

dashboard:
	cd dashboard && pnpm dev

dev:
	$(MAKE) -j2 api dashboard

test:
	$(ENGINE) ruff check .
	$(ENGINE) ruff format --check .
	$(ENGINE) mypy src/engine
	$(ENGINE) pytest
	cd dashboard && pnpm typecheck
	cd dashboard && pnpm lint --max-warnings=0
