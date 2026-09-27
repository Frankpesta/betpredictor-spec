# SportyBet booking — page structure and decision (2026-09-27)

## Page structure (event page, headless inspection; saved HTML in
`data/raw/sportybet/2026-09-27/dom/event_page.html`)
- Odds buttons have **no stable ids or data attributes**. An Asian Handicap
  outcome is only
  `div.m-table-row.m-outcome > div.m-table-cell > span.m-table-cell-item` with text
  "Home (-0.5)", under a header `span.m-table-header-title` "Asian Handicap -0.5".
  Market id (16), outcome id (1714) and specifier never appear in the DOM.
- The betslip does have stable attributes:
  - Book Bet: `[data-cms-key="book_bet"][data-cms-page="component_betslip"]`
  - **Place Bet: `[data-op="desktop-betslip-place-bet-button"]` — must never be touched.**
- So docs/04 §3.3 ("stable attributes, never visible text alone") cannot be met for
  selecting outcomes by clicking.

## Decision (user, 2026-09-27): API + manual fallback
- `make book` uses the booking endpoint found in discovery (F11), which is the same
  call the site's own "Book Bet" button makes and works without login:
  1. `POST /api/ng/factsCenter/Outcomes` with the slip's selections → current odds
     and active status → docs/04 §3.4 drift rule (> `booking.max_odds_drift`, or
     suspended → `failed`, no booking attempted);
  2. `POST /api/ng/orders/share` → `data.shareCode`; the echoed
     `ticket.selections` must equal what we sent.
- Any failure → `booking_status = failed` with the reason; the dashboard shows the
  legs for manual entry (`booking_status = manual`, docs/04 §3.5).
- **No UI-clicking path is implemented**, so the engine has no code that could ever
  reach the Place Bet button. `sportybet/selectors.py` records the selectors above
  for reference only.
