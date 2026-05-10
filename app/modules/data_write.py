from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medical_record import MedicalRecord
from app.modules.data_retrieval import DataRetrievalModule
from app.modules.ledger_interface import LedgerInterfaceModule


class DataWriteModule:
    """
    Writes a clinical record to the Medical Database and atomically commits
    a DATA_WRITE entry to the Records Ledger within the same transaction.

    If either the DB insert or the ledger append fails, both are rolled back.
    """

    def __init__(
        self,
        retrieval: DataRetrievalModule,
        ledger: LedgerInterfaceModule,
    ) -> None:
        self._retrieval = retrieval
        self._ledger = ledger

    async def write_record(
        self,
        session_id: str,
        doctor_id: str,
        record_type: str,
        content: dict,
        db: AsyncSession,
    ) -> tuple[uuid.UUID, str, int]:
        """
        Returns (record_id, content_hash, ledger_block_index).
        The caller must commit the transaction after this call.
        """
        med_id = await self._retrieval.resolve_med_id(session_id)
        content_hash = hashlib.sha256(
            json.dumps(content, sort_keys=True).encode()
        ).hexdigest()

        record = MedicalRecord(
            med_id=uuid.UUID(med_id) if isinstance(med_id, str) else med_id,
            record_type=record_type,
            content=content,
            content_hash=content_hash,
            doctor_id=doctor_id,
        )
        db.add(record)
        await db.flush()  # assign record.id without committing

        block_index = await self._ledger.append_records_entry(
            db,
            event_type="DATA_WRITE",
            session_id=session_id,
            doctor_id=doctor_id,
            content_hash=content_hash,
        )

        return record.id, content_hash, block_index
