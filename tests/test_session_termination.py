from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.session_termination import SessionTerminationModule
from app.storage.medical_id_table import MedicalIDEntry, MedicalIDTable
from app.storage.redis_store import get_session, set_session


async def _setup_session(
    table: MedicalIDTable,
    redis,
    session_id: str = "s1",
    med_id: str = "med-123",
) -> MedicalIDEntry:
    entry = MedicalIDEntry(
        temp_id="temp-abc",
        med_id=med_id,
        pinfo={"name": "Alice"},
        expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
    )
    await table.insert(session_id, entry)
    await set_session(redis, session_id, {"status": "ACTIVE", "doctor_id": "dr-1"}, ttl=3600)
    return entry


async def test_normal_termination_clears_memory_and_redis(
    test_table: MedicalIDTable,
    fake_redis,
    db_session: AsyncSession,
) -> None:
    from app.modules.ledger_interface import LedgerInterfaceModule
    ledger = LedgerInterfaceModule()
    termination = SessionTerminationModule(test_table, ledger)

    await _setup_session(test_table, fake_redis)

    await termination.terminate(
        session_id="s1",
        event_type="SESSION_CLOSE",
        redis=fake_redis,
        db=db_session,
        doctor_id="dr-1",
    )

    assert await test_table.get_by_session("s1") is None
    assert await get_session(fake_redis, "s1") is None


async def test_memory_wiped_even_when_ledger_fails(
    test_table: MedicalIDTable,
    fake_redis,
    db_session: AsyncSession,
) -> None:
    """Invariant #7: sensitive data must be cleared before ledger write."""
    mock_ledger = MagicMock()
    mock_ledger.append_records_entry = AsyncMock(
        side_effect=RuntimeError("Ledger unavailable")
    )

    termination = SessionTerminationModule(test_table, mock_ledger)
    entry = await _setup_session(test_table, fake_redis, "s-fail")

    # The ledger failure should propagate, but memory/Redis must be cleared first
    with pytest.raises(RuntimeError, match="Ledger unavailable"):
        await termination.terminate(
            session_id="s-fail",
            event_type="SESSION_CLOSE",
            redis=fake_redis,
            db=db_session,
        )

    # Despite ledger failure, sensitive data is gone
    assert await test_table.get_by_session("s-fail") is None
    assert await get_session(fake_redis, "s-fail") is None


async def test_patient_revocation_logs_correct_event(
    test_table: MedicalIDTable,
    fake_redis,
    db_session: AsyncSession,
) -> None:
    from app.modules.ledger_interface import LedgerInterfaceModule
    from sqlalchemy import select
    from app.models.records_ledger import RecordsLedgerEntry

    ledger = LedgerInterfaceModule()
    termination = SessionTerminationModule(test_table, ledger)
    await _setup_session(test_table, fake_redis, "s-revoke")

    await termination.terminate(
        session_id="s-revoke",
        event_type="PATIENT_REVOCATION",
        redis=fake_redis,
        db=db_session,
        patient_id="patient-001",
    )

    result = await db_session.execute(select(RecordsLedgerEntry))
    entries = list(result.scalars().all())
    assert any(e.event_type == "PATIENT_REVOCATION" for e in entries)


async def test_session_close_logs_correct_event(
    test_table: MedicalIDTable,
    fake_redis,
    db_session: AsyncSession,
) -> None:
    from app.modules.ledger_interface import LedgerInterfaceModule
    from sqlalchemy import select
    from app.models.records_ledger import RecordsLedgerEntry

    ledger = LedgerInterfaceModule()
    termination = SessionTerminationModule(test_table, ledger)
    await _setup_session(test_table, fake_redis, "s-close")

    await termination.terminate(
        session_id="s-close",
        event_type="SESSION_CLOSE",
        redis=fake_redis,
        db=db_session,
        doctor_id="dr-1",
    )

    result = await db_session.execute(select(RecordsLedgerEntry))
    entries = list(result.scalars().all())
    assert any(e.event_type == "SESSION_CLOSE" for e in entries)


async def test_terminate_nonexistent_session_is_safe(
    test_table: MedicalIDTable,
    fake_redis,
    db_session: AsyncSession,
) -> None:
    from app.modules.ledger_interface import LedgerInterfaceModule
    ledger = LedgerInterfaceModule()
    termination = SessionTerminationModule(test_table, ledger)

    # Should not raise — deleting a non-existent session is a no-op for memory and Redis
    await termination.terminate(
        session_id="ghost-session",
        event_type="SESSION_CLOSE",
        redis=fake_redis,
        db=db_session,
    )
