from __future__ import annotations

import hashlib
import hmac
import base64
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.middleware.mtls import verify_mtls
from app.models.medical_record import MedicalRecord
from app.modules import get_ledger
from app.modules.ledger_interface import LedgerInterfaceModule
from app.schemas.cross_hospital import PeerDataRequest, RecordIndexEntry, CrossHospitalIndexResponse
from app.storage.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["cross-hospital-peer"])


@router.post("/cross-hospital/data", response_model=CrossHospitalIndexResponse)
async def serve_cross_hospital_data(
    body: PeerDataRequest,
    requesting_hospital: str = Depends(verify_mtls),
    db: AsyncSession = Depends(get_db),
    ledger: LedgerInterfaceModule = Depends(get_ledger),
) -> CrossHospitalIndexResponse:
    """
    Serves a record index to a verified peer hospital.

    mTLS cert is verified by verify_mtls. Additionally, the requesting hospital
    must be present in our Trust Ledger with an active JOIN event.
    """
    # Get requesting hospital's public key from trust ledger for verification
    from app.models.trust_block import TrustLedgerBlock
    peer_hash = hashlib.sha256(requesting_hospital.encode()).hexdigest()
    result = await db.execute(
        select(TrustLedgerBlock)
        .where(TrustLedgerBlock.subject_hospital == peer_hash)
        .order_by(TrustLedgerBlock.block_index.desc())
        .limit(1)
    )
    trust_block = result.scalar_one_or_none()
    if trust_block is None or trust_block.event_type != "JOIN" or not trust_block.subject_pubkey:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requesting hospital not found in Trust Ledger",
        )

    trusted = await ledger.verify_trust_ledger_for_peer(
        db, requesting_hospital, trust_block.subject_pubkey
    )
    if not trusted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Trust Ledger verification failed for requesting hospital",
        )

    # Verify request signature
    canonical = f"{body.session_id_hash}:{body.patient_id_hash}".encode()
    expected_sig = base64.b64encode(
        hmac.new(settings.kek_bytes, canonical, hashlib.sha256).digest()
    ).decode()
    if not hmac.compare_digest(body.signature, expected_sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid request signature",
        )

    # Log this cross-hospital access on our Records Ledger
    try:
        async with db.begin():
            await ledger.append_records_entry(
                db,
                event_type="CROSS_HOSPITAL_REQUEST",
                session_id=body.session_id_hash,  # already hashed by peer
                patient_id=body.patient_id_hash,
            )
    except Exception:
        logger.exception("Failed to log cross-hospital request to Records Ledger")

    # Return record index (no full content, no identity data)
    # Note: we'd normally look up records by patient_id_hash, but since we store by
    # med_id (not patient_id), this is a simplified implementation that returns
    # a placeholder. A full system would maintain a patient_id_hash → med_id mapping
    # established during prior sessions.
    return CrossHospitalIndexResponse(records=[])


@router.get("/cross-hospital/data/{record_id}")
async def serve_single_record(
    record_id: str,
    requesting_hospital: str = Depends(verify_mtls),
    db: AsyncSession = Depends(get_db),
    ledger: LedgerInterfaceModule = Depends(get_ledger),
) -> dict:
    """Serves a single record to a verified peer hospital."""
    import uuid as uuid_mod

    try:
        rid = uuid_mod.UUID(record_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid record_id")

    result = await db.execute(
        select(MedicalRecord).where(MedicalRecord.id == rid)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")

    return {
        "record_id": str(record.id),
        "record_type": record.record_type,
        "content": record.content,
        "content_hash": record.content_hash,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
