from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ledger_interface import LedgerInterfaceModule


# ── Records Ledger ────────────────────────────────────────────────────────────

async def test_first_entry_uses_genesis_hash(db_session: AsyncSession) -> None:
    ledger = LedgerInterfaceModule()
    async with db_session.begin():
        block_index = await ledger.append_records_entry(
            db_session, "SESSION_OPEN", "session-001"
        )
    assert block_index == 1

    from sqlalchemy import select
    from app.models.records_ledger import RecordsLedgerEntry
    result = await db_session.execute(select(RecordsLedgerEntry).where(RecordsLedgerEntry.block_index == 1))
    entry = result.scalar_one()
    assert entry.prev_hash == "0" * 64


async def test_sequential_chain(db_session: AsyncSession) -> None:
    ledger = LedgerInterfaceModule()
    block_hashes: list[str] = ["0" * 64]

    for i, event in enumerate(
        ["ACCESS_REQUEST", "SESSION_OPEN", "DATA_WRITE", "SESSION_CLOSE"], start=1
    ):
        async with db_session.begin():
            await ledger.append_records_entry(db_session, event, f"session-{i:03d}")

    from sqlalchemy import select
    from app.models.records_ledger import RecordsLedgerEntry
    result = await db_session.execute(
        select(RecordsLedgerEntry).order_by(RecordsLedgerEntry.block_index)
    )
    entries = list(result.scalars().all())
    assert len(entries) == 4

    for i, entry in enumerate(entries):
        expected_prev = block_hashes[i]
        assert entry.prev_hash == expected_prev, f"Block {i+1} has wrong prev_hash"
        block_hashes.append(entry.block_hash)


async def test_block_hash_changes_with_different_events(db_session: AsyncSession) -> None:
    ledger = LedgerInterfaceModule()
    async with db_session.begin():
        idx1 = await ledger.append_records_entry(db_session, "SESSION_OPEN", "s1")
    async with db_session.begin():
        idx2 = await ledger.append_records_entry(db_session, "SESSION_CLOSE", "s2")
    assert idx1 != idx2

    from sqlalchemy import select
    from app.models.records_ledger import RecordsLedgerEntry
    result = await db_session.execute(
        select(RecordsLedgerEntry).order_by(RecordsLedgerEntry.block_index)
    )
    entries = list(result.scalars().all())
    assert entries[0].block_hash != entries[1].block_hash


async def test_content_hash_stored_on_data_write(db_session: AsyncSession) -> None:
    ledger = LedgerInterfaceModule()
    async with db_session.begin():
        await ledger.append_records_entry(
            db_session,
            "DATA_WRITE",
            "session-001",
            content_hash="abc123" * 10 + "abcd",
        )

    from sqlalchemy import select
    from app.models.records_ledger import RecordsLedgerEntry
    result = await db_session.execute(select(RecordsLedgerEntry))
    entry = result.scalar_one()
    assert entry.content_hash is not None


async def test_patient_id_is_hashed(db_session: AsyncSession) -> None:
    import hashlib
    ledger = LedgerInterfaceModule()
    async with db_session.begin():
        await ledger.append_records_entry(
            db_session,
            "APPROVAL",
            "session-001",
            patient_id="patient-plaintext-id",
        )

    from sqlalchemy import select
    from app.models.records_ledger import RecordsLedgerEntry
    result = await db_session.execute(select(RecordsLedgerEntry))
    entry = result.scalar_one()
    expected_hash = hashlib.sha256("patient-plaintext-id".encode()).hexdigest()
    assert entry.patient_id_hash == expected_hash
    # Plaintext patient ID must NOT be stored
    assert "patient-plaintext-id" not in (entry.patient_id_hash or "")


# ── Trust Ledger ──────────────────────────────────────────────────────────────

async def _add_trust_join(db_session: AsyncSession, hospital_id: str, pubkey: str = "pubkey-pem") -> int:
    ledger = LedgerInterfaceModule()
    async with db_session.begin():
        return await ledger.append_trust_block(
            db_session,
            event_type="JOIN",
            subject_hospital_id=hospital_id,
            subject_pubkey_pem=pubkey,
            approved_by=["hospital-001"],
        )


async def test_trust_ledger_join_accepted(db_session: AsyncSession) -> None:
    block_index = await _add_trust_join(db_session, "peer-hospital")
    assert block_index == 1


async def test_trust_ledger_verify_join(db_session: AsyncSession) -> None:
    await _add_trust_join(db_session, "peer-hospital", pubkey="test-pub-key")
    ledger = LedgerInterfaceModule()
    result = await ledger.verify_trust_ledger_for_peer(db_session, "peer-hospital", "test-pub-key")
    assert result is True


async def test_trust_ledger_verify_leave_after_join(db_session: AsyncSession) -> None:
    await _add_trust_join(db_session, "peer-hospital", pubkey="test-pub-key")

    ledger = LedgerInterfaceModule()
    async with db_session.begin():
        await ledger.append_trust_block(
            db_session,
            event_type="LEAVE",
            subject_hospital_id="peer-hospital",
            subject_pubkey_pem=None,
            approved_by=["hospital-001"],
        )

    result = await ledger.verify_trust_ledger_for_peer(db_session, "peer-hospital", "test-pub-key")
    assert result is False


async def test_trust_ledger_unknown_peer_returns_false(db_session: AsyncSession) -> None:
    ledger = LedgerInterfaceModule()
    result = await ledger.verify_trust_ledger_for_peer(db_session, "unknown-hospital", "pubkey")
    assert result is False


async def test_trust_ledger_pubkey_mismatch_returns_false(db_session: AsyncSession) -> None:
    await _add_trust_join(db_session, "peer-hospital", pubkey="correct-pubkey")
    ledger = LedgerInterfaceModule()
    result = await ledger.verify_trust_ledger_for_peer(db_session, "peer-hospital", "wrong-pubkey")
    assert result is False


async def test_trust_ledger_tampered_hash_rejected(db_session: AsyncSession) -> None:
    await _add_trust_join(db_session, "peer-hospital", pubkey="test-pub-key")

    # Tamper with the stored block_hash directly in the DB
    from sqlalchemy import update
    from app.models.trust_block import TrustLedgerBlock
    await db_session.execute(
        update(TrustLedgerBlock).values(block_hash="0" * 64)
    )
    await db_session.commit()

    ledger = LedgerInterfaceModule()
    result = await ledger.verify_trust_ledger_for_peer(db_session, "peer-hospital", "test-pub-key")
    assert result is False


async def test_get_records_entries_for_patient(db_session: AsyncSession) -> None:
    import hashlib
    ledger = LedgerInterfaceModule()
    patient_id = "patient-001"
    patient_hash = hashlib.sha256(patient_id.encode()).hexdigest()

    async with db_session.begin():
        await ledger.append_records_entry(db_session, "SESSION_OPEN", "s1", patient_id=patient_id)
        await ledger.append_records_entry(db_session, "SESSION_OPEN", "s2", patient_id="other-patient")

    entries = await ledger.get_records_entries_for_patient(db_session, patient_hash)
    assert len(entries) == 1
    assert entries[0].session_id_hash == hashlib.sha256("s1".encode()).hexdigest()
