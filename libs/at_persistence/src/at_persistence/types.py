"""Dialect-portable column types.

The production target is PostgreSQL and the schema uses ``UUID`` and ``JSONB``
natively. Tests benefit from being able to run against in-memory SQLite without a
container, so these wrappers emit the Postgres type on Postgres and a faithful
fallback elsewhere.

The Postgres DDL is never weakened: ``load_dialect_impl`` returns the exact native
type when the dialect is postgresql. Migrations and production are unaffected.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import CHAR, Dialect, Text, TypeDecorator
from sqlalchemy.dialects import postgresql


class GUID(TypeDecorator[uuid.UUID]):
    """UUID column: native ``uuid`` on Postgres, 36-char string elsewhere."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> uuid.UUID | None:
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class JSONBType(TypeDecorator[dict[str, Any]]):
    """JSON column: native ``jsonb`` on Postgres, serialised text elsewhere."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        return value


def enum_column(python_enum: type, name: str, dialect_hint: str = "") -> Any:
    """Enum column: native Postgres ENUM, VARCHAR with a check elsewhere.

    ``create_type=False`` because the enum types are created explicitly by the
    Alembic migration, ahead of any table that references them.
    """
    from sqlalchemy import Enum as SAEnum

    return SAEnum(
        python_enum,
        name=name,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
        create_type=False,
        native_enum=True,
    )
