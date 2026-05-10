from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CrossHospitalIndexRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    target_hospitals: list[str]


class RecordIndexEntry(BaseModel):
    record_id: str
    record_type: str
    created_at: datetime
    source_hospital: str


class CrossHospitalIndexResponse(BaseModel):
    records: list[RecordIndexEntry]


class CrossHospitalFetchResponse(BaseModel):
    record_id: str
    content: dict
    source_hospital: str
    verified: bool


class PeerDataRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    session_id_hash: str
    patient_id_hash: str
    requesting_hospital_id: str
    signature: str  # base64 HMAC-SHA256
