# 00 — Start Here

Spec pack for **BetPredictor**, a personal, local-only football value-betting
engine (Asian Handicap + Over/Under) for SportyBet Nigeria.

Read in this order:
1. `../CLAUDE.md` — rules, fixed stack, repo layout, commands
2. `01-architecture.md` — data flow, DB schema, config
3. `02-data-pipeline.md` — football-data.co.uk, Understat, team mapping
4. `03-model.md` — Dixon-Coles + xG, market maths, backtest and gate
5. `04-sportybet-integration.md` — discovery, odds, booking codes, results
6. `05-value-and-slips.md` — value detection, 2-odds / mid / mega slips
7. `06-tracking-settlement.md` — CLV, settlement, performance
8. `07-dashboard.md` — FastAPI + Next.js dashboard
9. `08-phases-and-acceptance.md` — build order and gates

## Kick-off prompt for Claude Code

> Read CLAUDE.md and every file in docs/ in numeric order. Summarise the
> plan back to me in under 20 lines, list any ambiguities you found, then
> start Phase 0. Stop at the end of each phase and show me the acceptance
> check outputs before continuing.
