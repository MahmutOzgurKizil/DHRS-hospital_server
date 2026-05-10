import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, LargeBinary, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, new_uuid


class DoctorKey(Base):
    __tablename__ = "doctor_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    doctor_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    doctor_name: Mapped[str] = mapped_column(String(200))
    # [12-byte nonce][AES-256-GCM(KEK, doctor_private_key_pem)]
    encrypted_dsk: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # RSA public key (plaintext) — used by App Server to encrypt enc_med_id
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
