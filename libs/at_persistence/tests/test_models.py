"""Schema tests for the persistence layer.

Two levels of assurance:

* **Dialect tests** compile the DDL against the PostgreSQL dialect and assert the
  production types (``UUID``, ``JSONB``, native enums) are emitted. These catch
  a portability shim accidentally weakening the real schema.
* **Round-trip tests** run against in-memory SQLite so ORM mapping, defaults and
  type marshalling are exercised in CI without a container. Postgres-specific DDL
  (hypertables, compression) is covered by the migration tests in M3.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from at_core.domain.enums import EngineModule, HealthBand, Severity, Subset, TwinStatus
from at_persistence import (
    Base,
    ComponentHealthTS,
    Engine,
    Fleet,
    Telemetry,
    TwinEvent,
    TwinSnapshot,
    User,
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as active:
        yield active
    await engine.dispose()


@pytest_asyncio.fixture
async def engine_row(session: AsyncSession) -> Engine:
    fleet = Fleet(name="Test Fleet")
    session.add(fleet)
    await session.flush()
    row = Engine(
        fleet_id=fleet.id,
        unit_number=27,
        subset=Subset.FD001,
        split="train",
        external_ref="FD001-train-U27",
        total_cycles=206,
        tail_number="AT-0027",
    )
    session.add(row)
    await session.flush()
    return row


# ── DDL: the production schema must stay native ──────────────────────────────


def compile_pg(table_name: str) -> str:
    return str(CreateTable(Base.metadata.tables[table_name]).compile(dialect=postgresql.dialect()))


def test_postgres_ddl_uses_native_uuid_and_jsonb() -> None:
    ddl = compile_pg("twin_snapshots")
    assert "id UUID NOT NULL" in ddl
    assert "component_health JSONB NOT NULL" in ddl


def test_postgres_ddl_uses_native_enum_types() -> None:
    ddl = compile_pg("twin_snapshots")
    assert "status twin_status NOT NULL" in ddl
    assert "health_band health_band NOT NULL" in ddl


def test_telemetry_has_all_21_sensor_columns() -> None:
    columns = Telemetry.__table__.columns
    for index in range(1, 22):
        assert f"s{index}" in columns, f"missing sensor column s{index}"


def test_telemetry_is_keyed_by_engine_and_cycle() -> None:
    """Natural key prevents duplicate rows if a replay is restarted."""
    primary = {column.name for column in Telemetry.__table__.primary_key}
    assert primary == {"engine_id", "cycle"}


def test_twin_events_are_keyed_by_engine_and_sequence() -> None:
    """Event sourcing requires a per-engine monotonic sequence to be unique."""
    primary = {column.name for column in TwinEvent.__table__.primary_key}
    assert primary == {"engine_id", "seq"}


def test_health_index_range_is_enforced_by_a_check_constraint() -> None:
    assert "ck_snapshot_health" in compile_pg("twin_snapshots")


def test_expected_tables_exist() -> None:
    assert set(Base.metadata.tables) == {
        "fleets",
        "engines",
        "telemetry",
        "twin_snapshots",
        "twin_events",
        "component_health_ts",
        "users",
    }


# ── round trip ───────────────────────────────────────────────────────────────


async def test_engine_round_trip(session: AsyncSession, engine_row: Engine) -> None:
    await session.commit()
    found = (
        await session.execute(select(Engine).where(Engine.external_ref == "FD001-train-U27"))
    ).scalar_one()
    assert found.subset is Subset.FD001
    assert isinstance(found.id, uuid.UUID)
    assert found.engine_model == "AT-9000"


async def test_engine_identity_is_unique(session: AsyncSession, engine_row: Engine) -> None:
    """Re-seeding must not silently create a duplicate twin for the same unit."""
    session.add(
        Engine(
            unit_number=27,
            subset=Subset.FD001,
            split="train",
            external_ref="FD001-train-U27-dup",
            total_cycles=206,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_telemetry_round_trip(session: AsyncSession, engine_row: Engine) -> None:
    session.add(
        Telemetry(
            engine_id=engine_row.id,
            cycle=1,
            ts=datetime.now(UTC),
            regime=0,
            op1=-0.0007,
            s3=1589.7,
            s11=47.47,
        )
    )
    await session.commit()
    row = (await session.execute(select(Telemetry))).scalar_one()
    assert row.s3 == pytest.approx(1589.7)
    assert row.regime == 0


async def test_event_payload_survives_the_json_round_trip(
    session: AsyncSession, engine_row: Engine
) -> None:
    session.add(
        TwinEvent(
            engine_id=engine_row.id,
            seq=1,
            cycle=178,
            event_type="twin.health.band_changed",
            severity=Severity.CRITICAL,
            payload={"from": "WARNING", "to": "CRITICAL", "health_index": 34.1},
        )
    )
    await session.commit()
    row = (await session.execute(select(TwinEvent))).scalar_one()
    assert row.payload["to"] == "CRITICAL"
    assert row.payload["health_index"] == pytest.approx(34.1)
    assert row.severity is Severity.CRITICAL


async def test_snapshot_round_trip(session: AsyncSession, engine_row: Engine) -> None:
    session.add(
        TwinSnapshot(
            engine_id=engine_row.id,
            cycle=50,
            seq=12,
            status=TwinStatus.RUNNING,
            health_index=72.4,
            health_band=HealthBand.WATCH,
            rul_p50=88.2,
            component_health={"HPC": 64.1, "FAN": 91.0},
            sensor_snapshot={"s3": 1591.2},
        )
    )
    await session.commit()
    row = (await session.execute(select(TwinSnapshot))).scalar_one()
    assert row.health_band is HealthBand.WATCH
    assert row.component_health["HPC"] == pytest.approx(64.1)


async def test_component_health_is_keyed_per_module(
    session: AsyncSession, engine_row: Engine
) -> None:
    now = datetime.now(UTC)
    session.add_all(
        [
            ComponentHealthTS(engine_id=engine_row.id, cycle=10, module=module, ts=now, score=80.0)
            for module in (EngineModule.HPC, EngineModule.FAN, EngineModule.HPT)
        ]
    )
    await session.commit()
    rows = (await session.execute(select(ComponentHealthTS))).scalars().all()
    assert len(rows) == 3
    assert {row.module for row in rows} == {
        EngineModule.HPC,
        EngineModule.FAN,
        EngineModule.HPT,
    }


async def test_cascade_delete_removes_dependent_rows(
    session: AsyncSession, engine_row: Engine
) -> None:
    """Deleting an engine must not strand orphan telemetry."""
    session.add(Telemetry(engine_id=engine_row.id, cycle=1, ts=datetime.now(UTC)))
    await session.commit()

    await session.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=ON"))
    await session.delete(engine_row)
    await session.commit()

    assert (await session.execute(select(Telemetry))).scalars().all() == []


async def test_user_email_is_unique(session: AsyncSession) -> None:
    session.add(User(email="a@example.com", password_hash="x"))
    await session.commit()
    session.add(User(email="a@example.com", password_hash="y"))
    with pytest.raises(IntegrityError):
        await session.commit()
