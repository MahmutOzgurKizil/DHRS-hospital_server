from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.storage.medical_id_table import MedicalIDEntry, MedicalIDTable


def _entry(
    temp_id: str = "t1",
    med_id: str = "med-123",
    pinfo: dict | None = None,
    expires_in: int = 3600,
) -> MedicalIDEntry:
    return MedicalIDEntry(
        temp_id=temp_id,
        med_id=med_id,
        pinfo=pinfo or {"name": "Alice"},
        expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in),
    )


async def test_insert_and_get_by_session(test_table: MedicalIDTable) -> None:
    await test_table.insert("s1", _entry("t1", "m1"))
    result = await test_table.get_by_session("s1")
    assert result is not None
    assert result.temp_id == "t1"
    assert result.med_id == "m1"


async def test_get_by_temp_id(test_table: MedicalIDTable) -> None:
    await test_table.insert("s1", _entry("t1", "m1"))
    pair = await test_table.get_by_temp_id("t1")
    assert pair is not None
    session_id, entry = pair
    assert session_id == "s1"
    assert entry.med_id == "m1"


async def test_get_by_temp_id_not_found(test_table: MedicalIDTable) -> None:
    result = await test_table.get_by_temp_id("nonexistent")
    assert result is None


async def test_get_by_session_not_found(test_table: MedicalIDTable) -> None:
    result = await test_table.get_by_session("ghost")
    assert result is None


async def test_delete_removes_entry(test_table: MedicalIDTable) -> None:
    await test_table.insert("s1", _entry())
    await test_table.delete("s1")
    assert await test_table.get_by_session("s1") is None


async def test_delete_wipes_sensitive_fields(test_table: MedicalIDTable) -> None:
    entry = _entry(med_id="very-sensitive-uuid", pinfo={"ssn": "secret"})
    # Keep a reference so we can inspect after deletion
    await test_table.insert("s1", entry)

    # Grab the entry object reference before deletion
    stored = await test_table.get_by_session("s1")
    assert stored is not None

    await test_table.delete("s1")

    # The entry object's sensitive fields should be zeroed
    assert all(c == "\x00" for c in stored.med_id)
    assert stored.pinfo == {}


async def test_delete_nonexistent_is_noop(test_table: MedicalIDTable) -> None:
    # Should not raise
    await test_table.delete("ghost-session")


async def test_expired_entry_not_returned(test_table: MedicalIDTable) -> None:
    entry = _entry(expires_in=-1)  # already expired
    await test_table.insert("s1", entry)
    result = await test_table.get_by_session("s1")
    assert result is None


async def test_purge_expired_removes_old_entries(test_table: MedicalIDTable) -> None:
    await test_table.insert("s1", _entry(expires_in=-1))
    await test_table.insert("s2", _entry(expires_in=3600))
    count = await test_table.purge_expired()
    assert count == 1
    assert await test_table.get_by_session("s1") is None
    assert await test_table.get_by_session("s2") is not None


async def test_add_and_get_cross_hospital_record(test_table: MedicalIDTable) -> None:
    await test_table.insert("s1", _entry())
    await test_table.add_cross_hospital_record("s1", "rec-1", {"data": "value"})
    rec = await test_table.get_cross_hospital_record("s1", "rec-1")
    assert rec == {"data": "value"}


async def test_cross_hospital_records_wiped_on_delete(test_table: MedicalIDTable) -> None:
    await test_table.insert("s1", _entry())
    await test_table.add_cross_hospital_record("s1", "rec-1", {"data": "sensitive"})
    stored = await test_table.get_by_session("s1")
    assert stored is not None
    await test_table.delete("s1")
    assert stored.cross_hospital_records == {}


async def test_concurrent_inserts_are_safe(test_table: MedicalIDTable) -> None:
    async def insert_one(i: int) -> None:
        await test_table.insert(f"s{i}", _entry(temp_id=f"t{i}", med_id=f"m{i}"))

    await asyncio.gather(*[insert_one(i) for i in range(50)])
    # All 50 entries should be present
    for i in range(50):
        entry = await test_table.get_by_session(f"s{i}")
        assert entry is not None, f"Missing entry for s{i}"
