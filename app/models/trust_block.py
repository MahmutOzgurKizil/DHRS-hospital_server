import uuid
from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, JSONB, new_uuid


class TrustLedgerBlock(Base):
    __tablename__ = "trust_ledger_blocks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    block_index: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(10), nullable=False)  # JOIN|LEAVE|REVOKE
    subject_hospital: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256
    subject_pubkey: Mapped[str | None] = mapped_column(Text, nullable=True)  # HPK PEM on JOIN
    approved_by: Mapped[list] = mapped_column(JSONB, nullable=False)
    block_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_block_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Exact isoformat string used during block_hash computation — must match on verify
    ledger_timestamp: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
