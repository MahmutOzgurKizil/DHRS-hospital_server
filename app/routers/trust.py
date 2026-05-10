from __future__ import annotations

import hashlib
import hmac
import base64
import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.middleware.mtls import verify_mtls
from app.modules import get_ledger
from app.modules.ledger_interface import LedgerInterfaceModule
from app.schemas.trust import MembershipRequest, MembershipResponse
from app.storage.database import get_db

router = APIRouter(tags=["trust"])


@router.post("/trust/membership", response_model=MembershipResponse)
async def handle_membership(
    body: MembershipRequest,
    hospital_id: str = Depends(verify_mtls),
    db: AsyncSession = Depends(get_db),
    ledger: LedgerInterfaceModule = Depends(get_ledger),
) -> MembershipResponse:
    """
    Receives a JOIN/LEAVE/REVOKE membership request from a peer hospital.

    The requesting hospital authenticates via mTLS. For JOIN events, the
    subject_pubkey_pem is stored in the Trust Ledger block.

    Majority voting is simplified here: any trusted peer can cast a vote
    and a single approval triggers the block write. Full voting logic is
    left for production hardening.
    """
    if body.event_type == "JOIN" and not body.subject_pubkey_pem:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="subject_pubkey_pem is required for JOIN events",
        )

    # Verify the requester's signature over the request body (HMAC-SHA256 with KEK)
    canonical = json.dumps(
        {
            "event_type": body.event_type,
            "subject_hospital_id": body.subject_hospital_id,
            "subject_pubkey_pem": body.subject_pubkey_pem,
            "requesting_hospital_id": body.requesting_hospital_id,
        },
        sort_keys=True,
    ).encode()
    expected_sig = base64.b64encode(
        hmac.new(settings.kek_bytes, canonical, hashlib.sha256).digest()
    ).decode()

    if not hmac.compare_digest(body.signature, expected_sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid membership request signature",
        )

    try:
        async with db.begin():
            block_index = await ledger.append_trust_block(
                db,
                event_type=body.event_type,
                subject_hospital_id=body.subject_hospital_id,
                subject_pubkey_pem=body.subject_pubkey_pem,
                approved_by=[hospital_id, settings.hospital_id],
            )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Ledger write failed: {exc}",
        ) from exc

    return MembershipResponse(
        accepted=True,
        block_index=block_index,
        message=f"{body.event_type} recorded at block {block_index}",
    )
