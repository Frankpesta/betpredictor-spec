# SportyBet settlement rules relevant to the tracker (docs/06 §3)

Verified **2026-09-27** from SportyBet's own pages (rendered headless; raw text saved in
`data/raw/sportybet/2026-09-27/rules/`):
- `https://www.sportybet.com/ng/m/help/about/terms-and-conditions/betting-procedures`
- `https://www.sportybet.com/ng/m/help/how-to-play/sports/general`
(The Zendesk copy requires login and was not used.)

## Verbatim rules
- **4.4** "Should a customer include a non-runner or void selection in a multiple
  bet/parlay, the bet will be settled on the remaining selections."
  → a void leg contributes multiplier **1** (docs/06 §3 matches).
- **4.3** "In multiple bets/parlays with a void selection(s), the "Potential Win" figure
  is reduced accordingly."
- **4.11** "In the event that any selection from a multiple bet is settled as LOST, the
  entire bet is lost." → any `loss` leg makes the slip `lost` immediately (docs/06 §3 matches).
- General: "If a match is not completed or not played (e.g. through disqualification,
  interruption, withdrawal, changes in draws, etc.) all undecided markets are considered void."
- **Friendlies exception:** "pre-match betting on friendly matches, where all match
  markets will be settled based on the actual duration of the match. Only bets on
  matches played for less than 45 minutes or more than 120 minutes are considered void."
  (Relevant to INTL friendlies — an abandoned friendly may still settle on its score.)
- AH quarter-line settlement (market guide on every AH market): half won / half lost,
  whole lines void on a push — matches docs/03 §8.

## Not stated on these pages
- How a **half-won / half-lost** leg is combined inside a multiple (v1 slips use half
  lines only, so this cannot occur yet; docs/06 multiplier rule is used).
- A **time limit for postponed matches** before bets are voided. The tracker therefore
  never guesses: a leg is voided only when SportyBet's result data says the match was
  cancelled/postponed/abandoned (statuses recorded as they are first seen); otherwise
  it stays pending and is reported.
