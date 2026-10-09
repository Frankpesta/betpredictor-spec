"""odds_snapshots / value_legs accept team goals, BTTS, 1X2 and double chance (docs/05 §10)

User request 2026-10-08; SportyBet ids verified in
docs/discovered/sportybet/2026-10-08/new-markets.md. Only the market/selection CHECK
constraints change. Views are dropped and recreated around the batch table rebuild.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-08
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_MARKETS = "'OU', 'AH'"
NEW_MARKETS = "'OU', 'AH', 'OU_HOME', 'OU_AWAY', '1X2', 'DC', 'BTTS'"
OLD_SELECTIONS = "'over', 'under', 'home', 'away'"
NEW_SELECTIONS = (
    "'over', 'under', 'home', 'away', 'draw', 'home_draw', 'home_away', 'draw_away', 'yes', 'no'"
)


def _load_0006() -> ModuleType:
    """The views as 0006 created them (versions/ is not a package: load by path)."""
    path = Path(__file__).with_name("0006_selection_strategy.py")
    spec = importlib.util.spec_from_file_location("_migration_0006", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v6 = _load_0006()


def _checks(markets: str, selections: str) -> None:
    _v6._drop_views()
    for table in ("odds_snapshots", "value_legs"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            # short names: the metadata naming convention adds "ck_<table>_"
            batch_op.drop_constraint("market", type_="check")
            batch_op.drop_constraint("selection", type_="check")
            batch_op.create_check_constraint("market", f"market IN ({markets})")
            batch_op.create_check_constraint("selection", f"selection IN ({selections})")
    _v6._views(with_strategy=True)


def upgrade() -> None:
    _checks(NEW_MARKETS, NEW_SELECTIONS)


def downgrade() -> None:
    # fails on purpose if rows of the new markets exist (CHECK on the rebuilt table)
    _checks(OLD_MARKETS, OLD_SELECTIONS)
