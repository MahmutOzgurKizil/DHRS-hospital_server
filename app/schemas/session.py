from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    appointment_id: str
    doctor_id: str


class CreateSessionResponse(BaseModel):
    session_id: str
    qr_payload: str  # base64-encoded JSON with HMAC signature
    expires_at: datetime


class AuthorizeSessionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    session_id: str
    enc_pinfo: str   # base64: [2B key_len][RSA-OAEP(HSK,aes_key)][12B nonce][GCM(aes_key,pinfo_json)]
    enc_med_id: str  # base64: RSA-OAEP(doctor_pubkey, med_id_bytes)


class AuthorizeSessionResponse(BaseModel):
    temp_id: str  # returned to doctor terminal for use in X-Temp-Id header
    status: str   # "ACTIVE"


class RecordSummary(BaseModel):
    id: str
    record_type: str
    content_hash: str
    doctor_id: str
    created_at: datetime


class SessionDataResponse(BaseModel):
    pinfo: dict         # decrypted patient info; never contains med_id
    records: list[RecordSummary]


class ConsentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    consent_granted: bool
    target_hospitals: list[str]


class RevokeRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    reason: str | None = None
