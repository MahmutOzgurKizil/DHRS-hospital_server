from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class MembershipRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_type: Literal["JOIN", "LEAVE", "REVOKE"]
    subject_hospital_id: str
    subject_pubkey_pem: str | None = None  # required for JOIN
    requesting_hospital_id: str
    signature: str  # base64; HMAC-SHA256 over canonical JSON of other fields


class MembershipResponse(BaseModel):
    accepted: bool
    block_index: int | None = None
    message: str
