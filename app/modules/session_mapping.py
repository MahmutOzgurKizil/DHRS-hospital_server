from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.storage.medical_id_table import MedicalIDEntry, MedicalIDTable, medical_id_table


class SessionMappingModule:
    """
    Generates TempID and writes the in-memory session→identity mapping.

    TempID is a 32-byte cryptographically random hex string.
    It is returned to the authorize endpoint caller and stored ONLY in the
    in-memory Medical ID Table — never in Redis, never in the database.
    """

    def __init__(self, table: MedicalIDTable = medical_id_table) -> None:
        self._table = table

    async def create_mapping(
        self,
        session_id: str,
        med_id: str,
        pinfo: dict,
    ) -> str:
        temp_id = secrets.token_hex(32)
        expires_at = datetime.now(tz=timezone.utc) + timedelta(
            seconds=settings.memory_table_ttl_seconds
        )
        entry = MedicalIDEntry(
            temp_id=temp_id,
            med_id=med_id,
            pinfo=pinfo,
            expires_at=expires_at,
        )
        await self._table.insert(session_id, entry)
        return temp_id
