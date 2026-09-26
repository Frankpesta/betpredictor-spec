"""Declarative base, column types and conventions shared by every table.

Conventions (docs/01 §3): integer PK `id`, UTC `created_at`/`updated_at` on every
table, enums as TEXT + CHECK, floats as REAL, JSON as TEXT.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    INTEGER,
    TIMESTAMP,
    CheckConstraint,
    Dialect,
    MetaData,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetime stored as naive UTC TIMESTAMP text.

    Writing a naive datetime is an error: every timestamp must be explicit UTC.
    """

    impl = TIMESTAMP
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"naive datetime {value!r}: timestamps must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class IntBool(TypeDecorator[bool]):
    """Boolean stored as INTEGER 0/1 (pair it with a CHECK via `bool_check`)."""

    impl = INTEGER
    cache_ok = True

    def process_bind_param(self, value: bool | None, dialect: Dialect) -> int | None:
        return None if value is None else int(bool(value))

    def process_result_value(self, value: int | None, dialect: Dialect) -> bool | None:
        return None if value is None else bool(value)


def utcnow() -> datetime:
    return datetime.now(UTC)


def enum_check(column: str, values: Iterable[str]) -> CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=column)


def bool_check(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN (0, 1)", name=column)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime}


class IdTimestampMixin:
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.current_timestamp(),
    )
