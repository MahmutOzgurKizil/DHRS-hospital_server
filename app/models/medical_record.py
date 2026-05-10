import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, JSONB, new_uuid


class MedicalRecord(Base):
    __tablename__ = "medical_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    # Indexed by med_id ONLY — no FK to any identity table
    med_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(50))
    content: Mapped[dict] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    doctor_id: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
