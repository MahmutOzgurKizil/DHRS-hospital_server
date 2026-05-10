from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.records_ledger import RecordsLedgerEntry
from app.models.trust_block import TrustLedgerBlock


class LedgerInterfaceModule:
    """
    Sole writer to both distributed ledgers.

    Every append method MUST be called within an open SQLAlchemy transaction
    (the caller owns commit/rollback). This allows DataWriteModule to include
    a ledger entry atomically with the medical record insert.

    Hash chain atomicity: SELECT ... FOR UPDATE NOWAIT on PostgreSQL ensures
    no two concurrent transactions share the same prev_hash. On SQLite (tests)
    the lock is omitted — PostgreSQL is required in production.
    """

    # ── Records Ledger ──────────────────────────────────────────────────────

    async def append_records_entry(
        self,
        db: AsyncSession,
        event_type: str,
        session_id: str,
        patient_id: str | None = None,
        doctor_id: str | None = None,
        content_hash: str | None = None,
    ) -> int:
        """
        Appends one entry to the Records Ledger.
        Returns the assigned block_index.
        Must be called within an open db transaction.
        """
        prev_hash, prev_index = await self._get_records_prev(db)
        next_index = prev_index + 1

        session_id_hash = self._sha256(session_id)
        patient_id_hash = self._sha256(patient_id) if patient_id else None
        doctor_id_hash = self._sha256(doctor_id) if doctor_id else None
        hospital_id_hash = self._sha256(settings.hospital_id)
        timestamp = datetime.now(tz=timezone.utc).isoformat()

        block_hash = self._compute_records_hash(
            event_type=event_type,
            session_id_hash=session_id_hash,
            patient_id_hash=patient_id_hash,
            doctor_id_hash=doctor_id_hash,
            hospital_id_hash=hospital_id_hash,
            content_hash=content_hash,
            timestamp=timestamp,
            prev_hash=prev_hash,
        )

        entry = RecordsLedgerEntry(
            block_index=next_index,
            event_type=event_type,
            session_id_hash=session_id_hash,
            patient_id_hash=patient_id_hash,
            doctor_id_hash=doctor_id_hash,
            hospital_id_hash=hospital_id_hash,
            content_hash=content_hash,
            prev_hash=prev_hash,
            block_hash=block_hash,
            ledger_timestamp=timestamp,
        )
        db.add(entry)
        await db.flush()
        return next_index

    async def get_records_entries_for_patient(
        self, db: AsyncSession, patient_id_hash: str
    ) -> list[RecordsLedgerEntry]:
        result = await db.execute(
            select(RecordsLedgerEntry)
            .where(RecordsLedgerEntry.patient_id_hash == patient_id_hash)
            .order_by(RecordsLedgerEntry.block_index)
        )
        return list(result.scalars().all())

    # ── Trust Ledger ────────────────────────────────────────────────────────

    async def append_trust_block(
        self,
        db: AsyncSession,
        event_type: str,
        subject_hospital_id: str,
        subject_pubkey_pem: str | None,
        approved_by: list[str],
    ) -> int:
        """
        Appends one block to the Trust Ledger.
        Returns the assigned block_index.
        Must be called within an open db transaction.
        """
        prev_hash, prev_index = await self._get_trust_prev(db)
        next_index = prev_index + 1

        subject_hospital_hash = self._sha256(subject_hospital_id)
        approved_by_hashes = [self._sha256(h) for h in approved_by]
        timestamp = datetime.now(tz=timezone.utc).isoformat()

        block_hash = self._compute_trust_hash(
            block_index=next_index,
            event_type=event_type,
            subject_hospital=subject_hospital_hash,
            subject_pubkey=subject_pubkey_pem or "",
            approved_by=approved_by_hashes,
            timestamp=timestamp,
            prev_hash=prev_hash,
        )

        block = TrustLedgerBlock(
            block_index=next_index,
            event_type=event_type,
            subject_hospital=subject_hospital_hash,
            subject_pubkey=subject_pubkey_pem,
            approved_by=approved_by_hashes,
            block_hash=block_hash,
            prev_block_hash=prev_hash,
            ledger_timestamp=timestamp,
        )
        db.add(block)
        await db.flush()
        return next_index

    async def verify_trust_ledger_for_peer(
        self,
        db: AsyncSession,
        peer_hospital_id: str,
        peer_pubkey_pem: str,
    ) -> bool:
        """
        Returns True only if:
        1. The peer hospital's most recent Trust Ledger block is a JOIN event.
        2. The full hash chain is unbroken.
        3. The stored subject_pubkey matches peer_pubkey_pem.
        """
        subject_hash = self._sha256(peer_hospital_id)
        result = await db.execute(
            select(TrustLedgerBlock).order_by(TrustLedgerBlock.block_index)
        )
        all_blocks = list(result.scalars().all())

        if not all_blocks:
            return False

        # Replay hash chain over the entire ledger using the stored ledger_timestamp
        expected_prev = "0" * 64
        for block in all_blocks:
            computed = self._compute_trust_hash(
                block_index=block.block_index,
                event_type=block.event_type,
                subject_hospital=block.subject_hospital,
                subject_pubkey=block.subject_pubkey or "",
                approved_by=block.approved_by,
                timestamp=block.ledger_timestamp,
                prev_hash=block.prev_block_hash,
            )
            if computed != block.block_hash:
                return False
            if block.prev_block_hash != expected_prev:
                return False
            expected_prev = block.block_hash

        # Find the most recent block for this peer
        peer_blocks = [b for b in all_blocks if b.subject_hospital == subject_hash]
        if not peer_blocks:
            return False

        latest = peer_blocks[-1]
        if latest.event_type != "JOIN":
            return False

        # Verify public key matches
        if latest.subject_pubkey is None:
            return False
        return latest.subject_pubkey.strip() == peer_pubkey_pem.strip()

    # ── Internal helpers ─────────────────────────────────────────────────────

    async def _get_records_prev(self, db: AsyncSession) -> tuple[str, int]:
        """Returns (prev_hash, prev_block_index). Uses FOR UPDATE NOWAIT on PostgreSQL."""
        stmt = (
            select(RecordsLedgerEntry.block_hash, RecordsLedgerEntry.block_index)
            .order_by(RecordsLedgerEntry.block_index.desc())
            .limit(1)
        )
        if "postgresql" in settings.database_url:
            stmt = stmt.with_for_update(nowait=True)

        result = await db.execute(stmt)
        row = result.first()
        if row is None:
            return "0" * 64, 0
        return row.block_hash, row.block_index

    async def _get_trust_prev(self, db: AsyncSession) -> tuple[str, int]:
        stmt = (
            select(TrustLedgerBlock.block_hash, TrustLedgerBlock.block_index)
            .order_by(TrustLedgerBlock.block_index.desc())
            .limit(1)
        )
        if "postgresql" in settings.database_url:
            stmt = stmt.with_for_update(nowait=True)

        result = await db.execute(stmt)
        row = result.first()
        if row is None:
            return "0" * 64, 0
        return row.block_hash, row.block_index

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _compute_records_hash(
        event_type: str,
        session_id_hash: str,
        patient_id_hash: str | None,
        doctor_id_hash: str | None,
        hospital_id_hash: str | None,
        content_hash: str | None,
        timestamp: str,
        prev_hash: str,
    ) -> str:
        payload = {
            "event_type": event_type,
            "session_id_hash": session_id_hash,
            "patient_id_hash": patient_id_hash,
            "doctor_id_hash": doctor_id_hash,
            "hospital_id_hash": hospital_id_hash,
            "content_hash": content_hash,
            "timestamp": timestamp,
            "prev_hash": prev_hash,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()

    @staticmethod
    def _compute_trust_hash(
        block_index: int,
        event_type: str,
        subject_hospital: str,
        subject_pubkey: str,
        approved_by: list[str],
        timestamp: str,
        prev_hash: str,
    ) -> str:
        payload = {
            "block_index": block_index,
            "event_type": event_type,
            "subject_hospital": subject_hospital,
            "subject_pubkey": subject_pubkey,
            "approved_by": sorted(approved_by),
            "timestamp": timestamp,
            "prev_hash": prev_hash,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
