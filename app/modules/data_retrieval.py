from __future__ import annotations

import hmac
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medical_record import MedicalRecord
from app.storage.medical_id_table import MedicalIDTable, medical_id_table


class SessionNotFoundError(Exception):
    pass


class TempIDMismatchError(Exception):
    pass


class DataRetrievalModule:
    def __init__(self, table: MedicalIDTable = medical_id_table) -> None:
        self._table = table

    async def get_session_data(
        self,
        session_id: str,
        temp_id: str,
        db: AsyncSession,
    ) -> tuple[dict[str, Any], list[MedicalRecord]]:
        """
        Returns (pinfo, records) for an active session.

        temp_id is validated with constant-time comparison to prevent timing attacks.
        med_id is resolved internally and never returned.
        """
        entry = await self._table.get_by_session(session_id)
        if entry is None:
            raise SessionNotFoundError(session_id)

        # Constant-time comparison prevents timing oracle on TempID
        if not hmac.compare_digest(entry.temp_id, temp_id):
            raise TempIDMismatchError("Invalid X-Temp-Id")

        result = await db.execute(
            select(MedicalRecord)
            .where(MedicalRecord.med_id == entry.med_id)
            .order_by(MedicalRecord.created_at)
        )
        records = list(result.scalars().all())
        return entry.pinfo, records

    async def resolve_med_id(self, session_id: str) -> str:
        entry = await self._table.get_by_session(session_id)
        if entry is None:
            raise SessionNotFoundError(session_id)
        return entry.med_id
