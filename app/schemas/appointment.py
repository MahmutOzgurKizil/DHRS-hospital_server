from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AppointmentNotifyRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    appointment_id: str
    doctor_id: str
    doctor_name: str
    scheduled_at: datetime


class AppointmentDetail(BaseModel):
    appointment_id: str
    doctor_id: str
    doctor_name: str
    scheduled_at: datetime


class AppointmentLookupResponse(BaseModel):
    appointments: list[AppointmentDetail]
