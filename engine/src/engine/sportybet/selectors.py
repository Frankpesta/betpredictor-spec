"""SportyBet page selectors — reference only (docs/discovered/sportybet/2026-09-27/booking.md).

The user chose API booking + manual fallback (2026-09-27), so no code drives the
betslip UI. These are recorded so a future UI path starts from verified facts.
Outcome buttons carry no stable attributes; only the betslip controls do.
"""

from __future__ import annotations

BOOK_BET = '[data-cms-key="book_bet"][data-cms-page="component_betslip"]'
# NEVER click this. Listed so any future UI code can assert it is not the target.
PLACE_BET_FORBIDDEN = '[data-op="desktop-betslip-place-bet-button"]'
OUTCOME_ROW = "div.m-table-row.m-outcome"  # no ids: structure + exact label only
MARKET_HEADER_TITLE = "span.m-table-header-title"
