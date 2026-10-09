"""odds_snapshots / value_legs accept half-time market codes (docs/10 §3)

`<full-time code>_1H` / `_2H`; selections are unchanged. SportyBet ids verified in
docs/discovered/sportybet/2026-10-08/new-markets.md (pcEvents probe 12:25Z).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-08
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FT = ("OU", "AH", "OU_HOME", "OU_AWAY", "1X2", "DC", "BTTS")
OLD_MARKETS = ", ".join(f"'{m}'" for m in FT)
NEW_MARKETS = ", ".join(f"'{m}'" for m in (*FT, *(f"{m}_{h}" for h in ("1H", "2H") for m in FT)))


def _load_0006() -> ModuleType:
    """The views as 0006 created them (versions/ is not a package: load by path)."""
    path = Path(__file__).with_name("0006_selection_strategy.py")
    spec = importlib.util.spec_from_file_location("_migration_0006", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _markets(markets: str) -> None:
    v6 = _load_0006()
    v6._drop_views()
    for table in ("odds_snapshots", "value_legs"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            # short name: the metadata naming convention adds "ck_<table>_"
            batch_op.drop_constraint("market", type_="check")
            batch_op.create_check_constraint("market", f"market IN ({markets})")
    v6._views(with_strategy=True)


def upgrade() -> None:
    _markets(NEW_MARKETS)


def downgrade() -> None:
    # fails on purpose if half-time rows exist (CHECK on the rebuilt table)
    _markets(OLD_MARKETS)
