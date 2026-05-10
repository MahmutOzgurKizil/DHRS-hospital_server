import uuid
from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, new_uuid


class RecordsLedgerEntry(Base):
    __tablename__ = "records_ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    # Manually assigned from MAX(block_index)+1 under SELECT FOR UPDATE — see LedgerInterfaceModule
    block_index: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    session_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    patient_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    doctor_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hospital_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    block_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Exact isoformat string used during block_hash computation — must match on verify
    ledger_timestamp: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
