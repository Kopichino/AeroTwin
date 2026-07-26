"""SQLAlchemy ORM models implementing the Doc 04 schema.

Only the tables needed through M3 are defined here; the ML, agent, knowledge and
maintenance tables arrive with their milestones. Timescale hypertable conversion
and continuous aggregates are applied by Alembic migrations, since they are DDL
operations SQLAlchemy does not model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from at_core.domain.enums import EngineModule, HealthBand, Severity, Subset, TwinStatus
from at_persistence.types import GUID, JSONBType, enum_column


class Base(DeclarativeBase):
    """Declarative base for every AeroTwin table."""


#: Enum columns render as native Postgres ENUMs; see at_persistence.types.
_enum = enum_column


class TimestampMixin:
    """Adds server-side created/updated timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Fleet(Base, TimestampMixin):
    __tablename__ = "fleets"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    operator: Mapped[str] = mapped_column(String(120), nullable=False, default="AeroTwin Airlines")
    base_airport: Mapped[str | None] = mapped_column(String(8))

    engines: Mapped[list[Engine]] = relationship(back_populates="fleet")


class Engine(Base, TimestampMixin):
    """One C-MAPSS trajectory, mirrored by exactly one digital twin."""

    __tablename__ = "engines"
    __table_args__ = (
        UniqueConstraint("subset", "split", "unit_number", name="uq_engine_identity"),
        CheckConstraint("split IN ('train','test')", name="ck_engine_split"),
        CheckConstraint("total_cycles >= 0", name="ck_engine_total_cycles"),
        Index("idx_engines_fleet", "fleet_id"),
        Index("idx_engines_subset_split", "subset", "split"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    fleet_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("fleets.id", ondelete="SET NULL")
    )
    unit_number: Mapped[int] = mapped_column(Integer, nullable=False)
    subset: Mapped[Subset] = mapped_column(_enum(Subset, "cmapss_subset"), nullable=False)
    split: Mapped[str] = mapped_column(String(8), nullable=False)
    external_ref: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    tail_number: Mapped[str | None] = mapped_column(String(16))
    engine_model: Mapped[str] = mapped_column(String(32), nullable=False, default="AT-9000")
    install_date: Mapped[datetime | None] = mapped_column(Date)
    total_cycles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    true_rul: Mapped[int | None] = mapped_column(Integer)

    fleet: Mapped[Fleet | None] = relationship(back_populates="engines")

    def __repr__(self) -> str:
        return f"<Engine {self.external_ref}>"


class Telemetry(Base):
    """Sensor readings, one row per engine cycle. Timescale hypertable on ``ts``."""

    __tablename__ = "telemetry"
    __table_args__ = (
        Index("idx_tel_engine_ts", "engine_id", "ts"),
        {"comment": "Hypertable: see migration for create_hypertable and compression"},
    )

    engine_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("engines.id", ondelete="CASCADE"),
        primary_key=True,
    )
    cycle: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    regime: Mapped[int | None] = mapped_column(SmallInteger)

    op1: Mapped[float | None] = mapped_column(Float)
    op2: Mapped[float | None] = mapped_column(Float)
    op3: Mapped[float | None] = mapped_column(Float)


# Sensor columns s1..s21 are attached programmatically: writing 21 near-identical
# declarations by hand invites typos and makes the schema harder to audit.
for _index in range(1, 22):
    setattr(
        Telemetry,
        f"s{_index}",
        mapped_column(f"s{_index}", Float, nullable=True),
    )


class TwinSnapshot(Base):
    """Periodic materialisation of twin state, enabling O(1) rehydration."""

    __tablename__ = "twin_snapshots"
    __table_args__ = (
        UniqueConstraint("engine_id", "cycle", name="uq_snapshot_engine_cycle"),
        CheckConstraint("health_index BETWEEN 0 AND 100", name="ck_snapshot_health"),
        Index("idx_snap_latest", "engine_id", "cycle"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    engine_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("engines.id", ondelete="CASCADE"), nullable=False
    )
    cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[TwinStatus] = mapped_column(_enum(TwinStatus, "twin_status"), nullable=False)
    health_index: Mapped[float] = mapped_column(Float, nullable=False)
    health_band: Mapped[HealthBand] = mapped_column(
        _enum(HealthBand, "health_band"), nullable=False
    )
    rul_p50: Mapped[float | None] = mapped_column(Float)
    rul_p10: Mapped[float | None] = mapped_column(Float)
    rul_p90: Mapped[float | None] = mapped_column(Float)
    regime: Mapped[int | None] = mapped_column(SmallInteger)
    anomaly_score: Mapped[float | None] = mapped_column(Float)
    component_health: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, nullable=False, default=dict
    )
    sensor_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TwinEvent(Base):
    """Append-only event log. Source of truth for twin state (ADR-004)."""

    __tablename__ = "twin_events"
    __table_args__ = (
        Index("idx_events_type_ts", "event_type", "ts"),
        Index("idx_events_engine_seq", "engine_id", "seq"),
    )

    engine_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("engines.id", ondelete="CASCADE"),
        primary_key=True,
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[Severity] = mapped_column(
        _enum(Severity, "severity"), nullable=False, default=Severity.INFO
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64))


class ComponentHealthTS(Base):
    """Per-module health over time, powering the component trend charts."""

    __tablename__ = "component_health_ts"

    engine_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("engines.id", ondelete="CASCADE"),
        primary_key=True,
    )
    cycle: Mapped[int] = mapped_column(Integer, primary_key=True)
    module: Mapped[EngineModule] = mapped_column(
        _enum(EngineModule, "engine_module"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    degradation_rate: Mapped[float | None] = mapped_column(Float)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="engineer")
