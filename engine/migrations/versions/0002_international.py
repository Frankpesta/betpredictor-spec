"""international football: matches.neutral, matches.fixture_key, nullable leagues.fd_code

docs/09 §3, §3a (user decision 2026-09-27).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_UQ = "uq_matches_league_id_home_team_id_away_team_id_season"
NEW_UQ = "uq_matches_league_id_home_team_id_away_team_id_season_fixture_key"


def upgrade() -> None:
    with op.batch_alter_table("leagues", schema=None) as batch_op:
        batch_op.alter_column("fd_code", existing_type=sa.Text(), nullable=True)

    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("neutral", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("fixture_key", sa.Text(), server_default="", nullable=False))
        batch_op.drop_constraint(OLD_UQ, type_="unique")
        batch_op.create_unique_constraint(
            NEW_UQ, ["league_id", "home_team_id", "away_team_id", "season", "fixture_key"]
        )
        batch_op.create_check_constraint("ck_matches_neutral", "neutral IN (0, 1)")


def downgrade() -> None:
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.drop_constraint("ck_matches_neutral", type_="check")
        batch_op.drop_constraint(NEW_UQ, type_="unique")
        batch_op.create_unique_constraint(
            OLD_UQ, ["league_id", "home_team_id", "away_team_id", "season"]
        )
        batch_op.drop_column("fixture_key")
        batch_op.drop_column("neutral")

    with op.batch_alter_table("leagues", schema=None) as batch_op:
        batch_op.alter_column("fd_code", existing_type=sa.Text(), nullable=False)
