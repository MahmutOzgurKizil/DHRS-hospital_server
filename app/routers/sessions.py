from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.middleware.session_auth import require_active_session
from app.models.doctor_key import DoctorKey
from app.modules import (
    get_cross_hospital,
    get_data_write,
    get_decryption_engine,
    get_ledger,
    get_retrieval,
    get_session_mapping,
    get_termination,
)
from app.modules.cross_hospital import CrossHospitalModule, TrustVerificationError
from app.modules.data_retrieval import DataRetrievalModule, SessionNotFoundError, TempIDMismatchError
from app.modules.data_write import DataWriteModule
from app.modules.decryption_engine import DecryptionEngine
from app.modules.ledger_interface import LedgerInterfaceModule
from app.modules.session_mapping import SessionMappingModule
from app.modules.session_termination import SessionTerminationModule
from app.schemas.cross_hospital import (
    CrossHospitalFetchResponse,
    CrossHospitalIndexRequest,
    CrossHospitalIndexResponse,
    RecordIndexEntry,
)
from app.schemas.record import WriteRecordRequest, WriteRecordResponse
from app.schemas.session import (
    AuthorizeSessionRequest,
    AuthorizeSessionResponse,
    ConsentRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    RecordSummary,
    RevokeRequest,
    SessionDataResponse,
)
from app.storage.database import get_db
from app.storage.redis_store import (
    delete_session,
    get_redis,
    get_session,
    set_session,
    update_session_status,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["sessions"])


def _build_qr_payload(session_id: str, appointment_id: str, doctor_id: str) -> str:
    payload = {
        "session_id": session_id,
        "hospital_id": settings.hospital_id,
        "appointment_id": appointment_id,
        "doctor_id": doctor_id,
        "timestamp": int(time.time()),
    }
    canonical = json.dumps(payload, sort_keys=True).encode()
    sig = hmac.new(settings.kek_bytes, canonical, hashlib.sha256).hexdigest()
    payload["sig"] = sig
    return base64.b64encode(json.dumps(payload).encode()).decode()


# ── Session creation ──────────────────────────────────────────────────────────

@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(
    body: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    ledger: LedgerInterfaceModule = Depends(get_ledger),
) -> CreateSessionResponse:
    """Doctor terminal initiates a session. Returns QR payload for patient to scan."""
    from datetime import datetime, timedelta, timezone

    session_id = str(uuid.uuid4())
    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=settings.session_ttl_seconds)

    session_data = {
        "status": "PENDING",
        "doctor_id": body.doctor_id,
        "appointment_id": body.appointment_id,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    await set_session(redis, session_id, session_data, settings.session_ttl_seconds)

    try:
        async with db.begin():
            await ledger.append_records_entry(
                db,
                event_type="ACCESS_REQUEST",
                session_id=session_id,
                doctor_id=body.doctor_id,
            )
    except Exception:
        logger.exception("Failed to log ACCESS_REQUEST for session %s", session_id)

    qr_payload = _build_qr_payload(session_id, body.appointment_id, body.doctor_id)
    return CreateSessionResponse(
        session_id=session_id,
        qr_payload=qr_payload,
        expires_at=expires_at,
    )


# ── Session authorization (called by App Server) ──────────────────────────────

@router.post("/sessions/authorize", response_model=AuthorizeSessionResponse)
async def authorize_session(
    body: AuthorizeSessionRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    engine: DecryptionEngine = Depends(get_decryption_engine),
    mapping: SessionMappingModule = Depends(get_session_mapping),
    ledger: LedgerInterfaceModule = Depends(get_ledger),
) -> AuthorizeSessionResponse:
    """
    App Server delivers encrypted patient info + medical ID.
    Triggers decryption, TempID mapping, and session activation.
    Returns TempID to the doctor terminal for use in X-Temp-Id header.
    """
    session_data = await get_session(redis, body.session_id)
    if not session_data or session_data.get("status") != "PENDING":
        try:
            async with db.begin():
                await ledger.append_records_entry(
                    db, "REJECTION", body.session_id
                )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session not found or not in PENDING state",
        )

    doctor_id = session_data["doctor_id"]

    # Load doctor's encrypted private key
    result = await db.execute(
        select(DoctorKey)
        .where(DoctorKey.doctor_id == doctor_id, DoctorKey.is_active.is_(True))
        .limit(1)
    )
    doctor_key_row = result.scalar_one_or_none()
    if doctor_key_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Doctor key not found",
        )

    # Decrypt — med_id and pinfo never leave this scope to any log/response
    try:
        pinfo = engine.decrypt_pinfo(body.enc_pinfo)
        med_id = engine.decrypt_med_id(body.enc_med_id, doctor_key_row.encrypted_dsk)
    except ValueError as exc:
        logger.warning("Decryption failed for session %s", body.session_id)
        try:
            async with db.begin():
                await ledger.append_records_entry(db, "REJECTION", body.session_id, doctor_id=doctor_id)
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Decryption failed: {exc}",
        ) from exc

    # Create in-memory TempID mapping (TempID never goes to Redis or DB)
    temp_id = await mapping.create_mapping(body.session_id, med_id, pinfo)

    # Activate session in Redis
    await update_session_status(redis, body.session_id, "ACTIVE")

    # Log APPROVAL + SESSION_OPEN
    try:
        async with db.begin():
            await ledger.append_records_entry(
                db, "APPROVAL", body.session_id, doctor_id=doctor_id
            )
            await ledger.append_records_entry(
                db, "SESSION_OPEN", body.session_id, doctor_id=doctor_id
            )
    except Exception:
        logger.exception("Failed to log APPROVAL/SESSION_OPEN for session %s", body.session_id)

    return AuthorizeSessionResponse(temp_id=temp_id, status="ACTIVE")


# ── Data retrieval ────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/data", response_model=SessionDataResponse)
async def get_session_data(
    session_id: str,
    x_temp_id: str = Header(alias="X-Temp-Id"),
    db: AsyncSession = Depends(get_db),
    _session: dict = Depends(require_active_session),
    retrieval: DataRetrievalModule = Depends(get_retrieval),
) -> SessionDataResponse:
    """
    Returns patient PINFO + local medical records for an active session.
    TempID is passed in X-Temp-Id header (never in URL to prevent access log leakage).
    """
    try:
        pinfo, records = await retrieval.get_session_data(session_id, x_temp_id, db)
    except SessionNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found in memory")
    except TempIDMismatchError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid TempID")

    return SessionDataResponse(
        pinfo=pinfo,
        records=[
            RecordSummary(
                id=str(r.id),
                record_type=r.record_type,
                content_hash=r.content_hash,
                doctor_id=r.doctor_id,
                created_at=r.created_at,
            )
            for r in records
        ],
    )


# ── Record write ──────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/records", response_model=WriteRecordResponse)
async def write_record(
    session_id: str,
    body: WriteRecordRequest,
    db: AsyncSession = Depends(get_db),
    session_data: dict = Depends(require_active_session),
    writer: DataWriteModule = Depends(get_data_write),
) -> WriteRecordResponse:
    """Writes a new clinical record; atomically commits its hash to the Records Ledger."""
    try:
        async with db.begin():
            record_id, content_hash, block_index = await writer.write_record(
                session_id=session_id,
                doctor_id=session_data["doctor_id"],
                record_type=body.record_type,
                content=body.content,
                db=db,
            )
    except SessionNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found in memory")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Record write failed: {exc}",
        ) from exc

    return WriteRecordResponse(
        record_id=str(record_id),
        content_hash=content_hash,
        ledger_block_index=block_index,
    )


# ── Session termination ───────────────────────────────────────────────────────

@router.delete("/sessions/{session_id}")
async def delete_session_endpoint(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    session_data: dict = Depends(require_active_session),
    termination: SessionTerminationModule = Depends(get_termination),
) -> Response:
    """Doctor-initiated session termination."""
    try:
        await termination.terminate(
            session_id=session_id,
            event_type="SESSION_CLOSE",
            redis=redis,
            db=db,
            doctor_id=session_data.get("doctor_id"),
        )
    except Exception:
        # Sensitive data is already gone even if ledger write failed
        logger.exception("Ledger write failed during session termination %s", session_id)
    return Response(status_code=204)


@router.post("/sessions/{session_id}/revoke")
async def revoke_session(
    session_id: str,
    body: RevokeRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _session: dict = Depends(require_active_session),
    termination: SessionTerminationModule = Depends(get_termination),
) -> Response:
    """Patient-initiated session revocation."""
    try:
        await termination.terminate(
            session_id=session_id,
            event_type="PATIENT_REVOCATION",
            redis=redis,
            db=db,
        )
    except Exception:
        logger.exception("Ledger write failed during patient revocation %s", session_id)
    return Response(status_code=204)


# ── Cross-hospital ────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/cross-hospital", response_model=CrossHospitalIndexResponse)
async def request_cross_hospital_index(
    session_id: str,
    body: CrossHospitalIndexRequest,
    db: AsyncSession = Depends(get_db),
    session_data: dict = Depends(require_active_session),
    ch_module: CrossHospitalModule = Depends(get_cross_hospital),
    ledger: LedgerInterfaceModule = Depends(get_ledger),
) -> CrossHospitalIndexResponse:
    """
    Requests a record index from specified peer hospitals.
    Results are held in memory only — never written to local Medical Database.
    """
    from app.storage.medical_id_table import medical_id_table
    entry = await medical_id_table.get_by_session(session_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found in memory")

    import hashlib as _hl
    patient_id_hash = _hl.sha256(entry.med_id.encode()).hexdigest()

    try:
        records = await ch_module.request_record_index(
            session_id=session_id,
            patient_id_hash=patient_id_hash,
            target_hospitals=body.target_hospitals,
            db=db,
        )
    except TrustVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    # Log cross-hospital request
    try:
        async with db.begin():
            await ledger.append_records_entry(
                db,
                event_type="CROSS_HOSPITAL_REQUEST",
                session_id=session_id,
                doctor_id=session_data.get("doctor_id"),
            )
    except Exception:
        logger.exception("Failed to log CROSS_HOSPITAL_REQUEST for session %s", session_id)

    return CrossHospitalIndexResponse(
        records=[
            RecordIndexEntry(
                record_id=r.get("record_id", ""),
                record_type=r.get("record_type", ""),
                created_at=r.get("created_at", ""),
                source_hospital=r.get("source_hospital", ""),
            )
            for r in records
        ]
    )


@router.get(
    "/sessions/{session_id}/cross-hospital/{record_id}",
    response_model=CrossHospitalFetchResponse,
)
async def fetch_cross_hospital_record(
    session_id: str,
    record_id: str,
    db: AsyncSession = Depends(get_db),
    session_data: dict = Depends(require_active_session),
    ch_module: CrossHospitalModule = Depends(get_cross_hospital),
) -> CrossHospitalFetchResponse:
    """Fetches a single external record; stored in memory only for session duration."""
    from app.storage.medical_id_table import medical_id_table
    entry = await medical_id_table.get_by_session(session_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found in memory")

    import hashlib as _hl
    patient_id_hash = _hl.sha256(entry.med_id.encode()).hexdigest()

    # Source hospital is embedded in the record_id prefix (simplified: full lookup needed in prod)
    source_hospital = record_id.split(":")[0] if ":" in record_id else ""

    try:
        record = await ch_module.fetch_single_record(
            session_id=session_id,
            record_id=record_id,
            source_hospital=source_hospital,
            patient_id_hash=patient_id_hash,
            db=db,
        )
    except TrustVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    return CrossHospitalFetchResponse(
        record_id=record_id,
        content=record.get("content", {}),
        source_hospital=record.get("source_hospital", source_hospital),
        verified=True,
    )


# ── Cross-hospital consent ────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/consent")
async def grant_consent(
    session_id: str,
    body: ConsentRequest,
    redis: aioredis.Redis = Depends(get_redis),
    _session: dict = Depends(require_active_session),
) -> Response:
    """
    Patient grants or revokes consent for cross-hospital data sharing.
    Consent state is stored in the Redis session entry.
    """
    session_data = await get_session(redis, session_id)
    if session_data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    ttl = await redis.ttl(f"session:{session_id}")
    session_data["consent_granted"] = body.consent_granted
    session_data["consented_hospitals"] = body.target_hospitals
    effective_ttl = ttl if ttl > 0 else settings.session_ttl_seconds
    await redis.set(
        f"session:{session_id}",
        json.dumps(session_data),
        ex=effective_ttl,
    )
    return Response(status_code=204)
