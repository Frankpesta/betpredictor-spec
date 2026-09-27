"""performance views v_leg_performance, v_slip_performance (docs/06 §4)

Both the engine and the dashboard read these, so they see identical numbers.
A leg appears once per slip it belongs to; profit is per 1 unit flat stake.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V_LEG = """
CREATE VIEW v_leg_performance AS
SELECT
    sl.id                          AS slip_leg_id,
    s.id                           AS slip_id,
    s.slip_type                    AS slip_type,
    s.pool                         AS pool,
    s.mode                         AS mode,
    l.key                          AS league,
    vl.market                      AS market,
    vl.line                        AS line,
    vl.selection                   AS selection,
    vl.odds                        AS odds,
    vl.p_final                     AS p_final,
    vl.edge                        AS edge_at_pick,
    sl.result                      AS result,
    sl.result_multiplier           AS multiplier,
    sl.result_multiplier - 1.0     AS profit,
    CASE sl.result WHEN 'win' THEN 1.0 WHEN 'half_win' THEN 0.5 ELSE 0.0 END AS win_units,
    sl.closing_odds                AS closing_odds,
    sl.clv                         AS clv,
    date(vl.created_at)            AS pick_date,
    m.kickoff_utc                  AS kickoff_utc,
    s.model_version                AS model_version
FROM slip_legs sl
JOIN slips s        ON s.id = sl.slip_id
JOIN value_legs vl  ON vl.id = sl.value_leg_id
JOIN matches m      ON m.id = vl.match_id
JOIN leagues l      ON l.id = m.league_id
WHERE sl.result <> 'pending'
"""

V_SLIP = """
CREATE VIEW v_slip_performance AS
SELECT
    s.id                           AS slip_id,
    s.slip_type                    AS slip_type,
    s.pool                         AS pool,
    s.slip_date                    AS slip_date,
    (SELECT count(*) FROM slip_legs x WHERE x.slip_id = s.id) AS legs,
    s.total_odds                   AS total_odds,
    s.p_all_win                    AS p_all_win,
    s.expected_multiplier          AS expected_multiplier,
    s.status                       AS status,
    s.return_multiplier            AS return_multiplier,
    s.return_multiplier - 1.0      AS profit,
    s.mode                         AS mode,
    s.stake                        AS stake,
    CASE WHEN s.mode = 'placed' AND s.stake IS NOT NULL
         THEN s.stake * (s.return_multiplier - 1.0) END AS profit_naira,
    s.booking_code                 AS booking_code,
    s.model_version                AS model_version
FROM slips s
WHERE s.status <> 'open'
"""


def upgrade() -> None:
    op.execute(V_LEG)
    op.execute(V_SLIP)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_slip_performance")
    op.execute("DROP VIEW IF EXISTS v_leg_performance")
