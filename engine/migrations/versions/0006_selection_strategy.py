"""slips.strategy + value_legs.qualifies; performance views expose strategy (docs/05 §8)

The user switched leg selection from "value" to "likeliest" on 2026-09-30. Slips record
which rule built them so paper-trading stats never mix the two; value legs record the
engine's eligibility decision (it depends on the match favourite, which the dashboard
cannot recompute from a single leg). SQLite cannot ALTER a view and batch mode rebuilds
the tables, so both views are dropped first and recreated with a `strategy` column.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEG_COLUMNS = """
    sl.id                          AS slip_leg_id,
    vl.id                          AS value_leg_id,
    m.id                           AS match_id,
    s.id                           AS slip_id,
    s.slip_type                    AS slip_type,
    s.pool                         AS pool,{strategy}
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

_SLIP_COLUMNS = """
    s.id                           AS slip_id,
    s.slip_type                    AS slip_type,
    s.pool                         AS pool,{strategy}
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

_STRATEGY = "\n    s.strategy                     AS strategy,"


def _views(with_strategy: bool) -> None:
    col = _STRATEGY if with_strategy else ""
    op.execute("CREATE VIEW v_leg_performance AS\nSELECT" + _LEG_COLUMNS.format(strategy=col))
    op.execute("CREATE VIEW v_slip_performance AS\nSELECT" + _SLIP_COLUMNS.format(strategy=col))


def _drop_views() -> None:
    op.execute("DROP VIEW IF EXISTS v_slip_performance")
    op.execute("DROP VIEW IF EXISTS v_leg_performance")


def upgrade() -> None:
    _drop_views()
    with op.batch_alter_table("slips", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("strategy", sa.Text(), server_default="value", nullable=False)
        )
        batch_op.create_check_constraint("ck_slips_strategy", "strategy IN ('value', 'likeliest')")
    with op.batch_alter_table("value_legs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("qualifies", sa.Integer(), nullable=True))
        batch_op.create_check_constraint("ck_value_legs_qualifies", "qualifies IN (0, 1)")
    _views(with_strategy=True)


def downgrade() -> None:
    _drop_views()
    with op.batch_alter_table("value_legs", schema=None) as batch_op:
        batch_op.drop_constraint("ck_value_legs_qualifies", type_="check")
        batch_op.drop_column("qualifies")
    with op.batch_alter_table("slips", schema=None) as batch_op:
        batch_op.drop_constraint("ck_slips_strategy", type_="check")
        batch_op.drop_column("strategy")
    _views(with_strategy=False)
