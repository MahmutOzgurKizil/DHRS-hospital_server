from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class MedicalIDEntry:
    temp_id: str
    med_id: str                                    # SECURITY: never logged, never persisted
    pinfo: dict[str, Any]                          # decrypted patient info
    cross_hospital_records: dict[str, Any] = field(default_factory=dict)
    expires_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


class MedicalIDTable:
    """
    In-memory mapping of session_id → {temp_id, med_id, pinfo, ...}.

    Never written to disk. A server restart wipes all entries and invalidates all sessions.
    All methods are async-safe via asyncio.Lock.
    """

    def __init__(self) -> None:
        self._table: dict[str, MedicalIDEntry] = {}
        self._lock = asyncio.Lock()

    async def insert(self, session_id: str, entry: MedicalIDEntry) -> None:
        async with self._lock:
            self._table[session_id] = entry

    async def get_by_session(self, session_id: str) -> MedicalIDEntry | None:
        async with self._lock:
            entry = self._table.get(session_id)
            if entry is None:
                return None
            if datetime.now(tz=timezone.utc) > entry.expires_at:
                self._wipe_entry(entry)
                del self._table[session_id]
                return None
            return entry

    async def get_by_temp_id(self, temp_id: str) -> tuple[str, MedicalIDEntry] | None:
        async with self._lock:
            now = datetime.now(tz=timezone.utc)
            for session_id, entry in list(self._table.items()):
                if now > entry.expires_at:
                    self._wipe_entry(entry)
                    del self._table[session_id]
                    continue
                if entry.temp_id == temp_id:
                    return session_id, entry
        return None

    async def add_cross_hospital_record(
        self, session_id: str, record_id: str, record: Any
    ) -> None:
        async with self._lock:
            entry = self._table.get(session_id)
            if entry is not None:
                entry.cross_hospital_records[record_id] = record

    async def get_cross_hospital_record(
        self, session_id: str, record_id: str
    ) -> Any | None:
        async with self._lock:
            entry = self._table.get(session_id)
            if entry is None:
                return None
            return entry.cross_hospital_records.get(record_id)

    async def delete(self, session_id: str) -> None:
        """Wipes sensitive fields in-place before removing the entry."""
        async with self._lock:
            entry = self._table.pop(session_id, None)
            if entry is not None:
                self._wipe_entry(entry)

    async def purge_expired(self) -> int:
        count = 0
        async with self._lock:
            now = datetime.now(tz=timezone.utc)
            expired = [sid for sid, e in self._table.items() if now > e.expires_at]
            for sid in expired:
                self._wipe_entry(self._table.pop(sid))
                count += 1
        return count

    @staticmethod
    def _wipe_entry(entry: MedicalIDEntry) -> None:
        entry.med_id = "\x00" * len(entry.med_id)
        entry.pinfo = {}
        entry.cross_hospital_records = {}
        entry.temp_id = "\x00" * len(entry.temp_id)


# Module-level singleton — never persisted, never replicated
medical_id_table = MedicalIDTable()
