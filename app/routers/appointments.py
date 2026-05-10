from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules import get_appointment
from app.modules.appointment_module import AppointmentModule
from app.schemas.appointment import (
    AppointmentDetail,
    AppointmentLookupResponse,
    AppointmentNotifyRequest,
)
from app.storage.database import get_db

router = APIRouter(tags=["appointments"])


@router.post("/appointments/notify")
async def notify_appointment(
    body: AppointmentNotifyRequest,
    db: AsyncSession = Depends(get_db),
    module: AppointmentModule = Depends(get_appointment),
) -> Response:
    """App Server registers a booked appointment. No patient identity stored."""
    async with db.begin():
        await module.register(
            appointment_id=body.appointment_id,
            doctor_id=body.doctor_id,
            doctor_name=body.doctor_name,
            scheduled_at=body.scheduled_at,
            db=db,
        )
    return Response(status_code=204)


@router.get("/appointments/register", response_model=AppointmentLookupResponse)
async def lookup_appointments(
    doctor_id: str | None = Query(default=None),
    day: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    module: AppointmentModule = Depends(get_appointment),
) -> AppointmentLookupResponse:
    """Registration desk lookup — returns appointments filtered by doctor and/or date."""
    appointments = await module.lookup(doctor_id=doctor_id, day=day, db=db)
    return AppointmentLookupResponse(
        appointments=[
            AppointmentDetail(
                appointment_id=a.appointment_id,
                doctor_id=a.doctor_id,
                doctor_name=a.doctor_name,
                scheduled_at=a.scheduled_at,
            )
            for a in appointments
        ]
    )
