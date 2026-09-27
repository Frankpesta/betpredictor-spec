"""slips.pool: club | intl (docs/09 §5 — INTL legs never mixed with club legs)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("slips", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pool", sa.Text(), server_default="club", nullable=False))
        batch_op.create_check_constraint("ck_slips_pool", "pool IN ('club', 'intl')")


def downgrade() -> None:
    with op.batch_alter_table("slips", schema=None) as batch_op:
        batch_op.drop_constraint("ck_slips_pool", type_="check")
        batch_op.drop_column("pool")
