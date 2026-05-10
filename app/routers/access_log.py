from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doctor_key import DoctorKey
from app.models.records_ledger import RecordsLedgerEntry
from app.storage.database import get_db

router = APIRouter(tags=["access-log"])


@router.get("/access-log")
async def get_access_log(
    patient_id: str = Query(..., description="Patient's own identifier"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Returns the patient's full session event history from the Records Ledger.

    The patient provides their own plaintext identifier; the server hashes it
    and matches against the ledger. Doctor display names are resolved where possible.
    """
    patient_id_hash = hashlib.sha256(patient_id.encode()).hexdigest()

    result = await db.execute(
        select(RecordsLedgerEntry)
        .where(RecordsLedgerEntry.patient_id_hash == patient_id_hash)
        .order_by(RecordsLedgerEntry.block_index)
    )
    entries = list(result.scalars().all())

    # Build a doctor_id_hash → name lookup from the doctor_keys table
    doctor_hashes = {e.doctor_id_hash for e in entries if e.doctor_id_hash}
    doctor_names: dict[str, str] = {}
    if doctor_hashes:
        dr_result = await db.execute(
            select(DoctorKey.doctor_id, DoctorKey.doctor_name).where(DoctorKey.is_active.is_(True))
        )
        for doctor_id, doctor_name in dr_result:
            h = hashlib.sha256(doctor_id.encode()).hexdigest()
            if h in doctor_hashes:
                doctor_names[h] = doctor_name

    events = []
    for e in entries:
        events.append(
            {
                "block_index": e.block_index,
                "event_type": e.event_type,
                "session_id_hash": e.session_id_hash,
                "doctor": doctor_names.get(e.doctor_id_hash or "", "Unknown"),
                "content_hash": e.content_hash,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
        )

    return {"events": events, "total": len(events)}
