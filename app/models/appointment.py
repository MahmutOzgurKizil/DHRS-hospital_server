import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, new_uuid


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    appointment_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    doctor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    doctor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
