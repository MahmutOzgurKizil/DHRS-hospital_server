from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment


class AppointmentModule:
    async def register(
        self,
        appointment_id: str,
        doctor_id: str,
        doctor_name: str,
        scheduled_at: datetime,
        db: AsyncSession,
    ) -> None:
        """Upsert an appointment notification (no patient identity stored)."""
        existing = await db.get(Appointment, appointment_id)
        if existing is not None:
            existing.doctor_id = doctor_id
            existing.doctor_name = doctor_name
            existing.scheduled_at = scheduled_at
        else:
            db.add(
                Appointment(
                    appointment_id=appointment_id,
                    doctor_id=doctor_id,
                    doctor_name=doctor_name,
                    scheduled_at=scheduled_at,
                )
            )
        await db.flush()

    async def lookup(
        self,
        doctor_id: str | None,
        day: date | None,
        db: AsyncSession,
    ) -> list[Appointment]:
        stmt = select(Appointment).order_by(Appointment.scheduled_at)
        if doctor_id:
            stmt = stmt.where(Appointment.doctor_id == doctor_id)
        if day:
            from sqlalchemy import func as sqlfunc
            stmt = stmt.where(
                sqlfunc.date(Appointment.scheduled_at) == day
            )
        result = await db.execute(stmt)
        return list(result.scalars().all())
