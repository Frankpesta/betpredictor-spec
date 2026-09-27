"""v_leg_performance gains value_leg_id + match_id (docs/06 §4)

A value leg can sit in several slips, so leg-level stats (flat-stake ROI, hit
rate, calibration) must count each value leg once; the view needs its id for that.
SQLite cannot ALTER a view: drop and recreate it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V_LEG_NEW = """
CREATE VIEW v_leg_performance AS
SELECT
    sl.id                          AS slip_leg_id,
    vl.id                          AS value_leg_id,
    m.id                           AS match_id,
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

V_LEG_OLD = """
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


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_leg_performance")
    op.execute(V_LEG_NEW)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_leg_performance")
    op.execute(V_LEG_OLD)
