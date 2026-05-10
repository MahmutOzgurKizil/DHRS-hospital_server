from __future__ import annotations

import logging

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ledger_interface import LedgerInterfaceModule
from app.storage.medical_id_table import MedicalIDTable, medical_id_table
from app.storage.redis_store import delete_session

logger = logging.getLogger(__name__)


class SessionTerminationModule:
    """
    Enforces invariant #7: sensitive memory MUST be cleared before any ledger write.

    Termination order (never change):
        1. Wipe Medical ID Table entry (in-memory data gone)
        2. Delete Redis session key
        3. Append SESSION_CLOSE or PATIENT_REVOCATION to Records Ledger

    If step 3 (ledger) fails, the session is still effectively terminated
    (steps 1 and 2 already completed). The caller receives the exception for
    logging/alerting but must NOT treat the session as still active.
    """

    def __init__(
        self,
        table: MedicalIDTable = medical_id_table,
        ledger: LedgerInterfaceModule | None = None,
    ) -> None:
        self._table = table
        self._ledger = ledger

    async def terminate(
        self,
        session_id: str,
        event_type: str,
        redis: aioredis.Redis,
        db: AsyncSession,
        doctor_id: str | None = None,
        patient_id: str | None = None,
    ) -> None:
        # Step 1: wipe in-memory entry FIRST
        await self._table.delete(session_id)

        # Step 2: remove Redis session
        await delete_session(redis, session_id)

        # Step 3: ledger write (failure here is safe — no sensitive data remains)
        ledger = self._ledger
        if ledger is None:
            from app.modules import get_ledger
            ledger = get_ledger()
        try:
            async with db.begin():
                await ledger.append_records_entry(
                    db,
                    event_type=event_type,
                    session_id=session_id,
                    patient_id=patient_id,
                    doctor_id=doctor_id,
                )
        except Exception:
            logger.exception(
                "Ledger write failed during termination of session %s. "
                "Session state has been cleared; ledger entry is missing.",
                session_id,
            )
            raise
