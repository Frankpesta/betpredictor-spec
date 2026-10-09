"""slips.strategy gains 'data_rule'; value_legs.qualify_reason (docs/05 §9)

On 2026-10-08 the user replaced "likeliest" with the data rule: every leg needs a
team-data reason (scoring chance + recent blanks), so the engine stores that reason
next to the eligibility decision for the dashboard. Views are unchanged but must be
dropped and recreated around the batch table rebuild (SQLite cannot ALTER a view).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-08
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
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


_v6 = _load_0006()


def _recreate(strategies: str) -> None:
    _v6._drop_views()
    with op.batch_alter_table("slips", schema=None) as batch_op:
        batch_op.drop_constraint("ck_slips_strategy", type_="check")
        batch_op.create_check_constraint("ck_slips_strategy", f"strategy IN ({strategies})")


def upgrade() -> None:
    _recreate("'value', 'likeliest', 'data_rule'")
    with op.batch_alter_table("value_legs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("qualify_reason", sa.Text(), nullable=True))
    _v6._views(with_strategy=True)


def downgrade() -> None:
    _recreate("'value', 'likeliest'")
    with op.batch_alter_table("value_legs", schema=None) as batch_op:
        batch_op.drop_column("qualify_reason")
    _v6._views(with_strategy=True)
