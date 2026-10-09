"""matches.ht_home_goals / ht_away_goals: first-half goals (docs/10 §1)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-08
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _load_0006() -> ModuleType:
    """The views as 0006 created them (versions/ is not a package: load by path)."""
    path = Path(__file__).with_name("0006_selection_strategy.py")
    spec = importlib.util.spec_from_file_location("_migration_0006", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def upgrade() -> None:
    # plain ADD COLUMN (nullable, no constraint): no table rebuild, views untouched
    op.add_column("matches", sa.Column("ht_home_goals", sa.Integer(), nullable=True))
    op.add_column("matches", sa.Column("ht_away_goals", sa.Integer(), nullable=True))


def downgrade() -> None:
    v6 = _load_0006()
    v6._drop_views()  # the batch rebuild of matches would break the views
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.drop_column("ht_away_goals")
        batch_op.drop_column("ht_home_goals")
    v6._views(with_strategy=True)
