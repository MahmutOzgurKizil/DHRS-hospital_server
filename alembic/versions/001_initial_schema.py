"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "medical_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("med_id", sa.Uuid(), nullable=False),
        sa.Column("record_type", sa.String(50), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("doctor_id", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_medical_records_med_id", "medical_records", ["med_id"])

    op.create_table(
        "doctor_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.String(100), nullable=False),
        sa.Column("doctor_name", sa.String(200), nullable=False),
        sa.Column("encrypted_dsk", sa.LargeBinary(), nullable=False),
        sa.Column("public_key_pem", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doctor_id"),
    )

    op.create_table(
        "trust_ledger_blocks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(10), nullable=False),
        sa.Column("subject_hospital", sa.String(64), nullable=False),
        sa.Column("subject_pubkey", sa.Text(), nullable=True),
        sa.Column("approved_by", postgresql.JSONB(), nullable=False),
        sa.Column("block_hash", sa.String(64), nullable=False),
        sa.Column("prev_block_hash", sa.String(64), nullable=False),
        sa.Column("ledger_timestamp", sa.String(50), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("block_index"),
    )

    op.create_table(
        "records_ledger_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("block_index", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("session_id_hash", sa.String(64), nullable=False),
        sa.Column("patient_id_hash", sa.String(64), nullable=True),
        sa.Column("doctor_id_hash", sa.String(64), nullable=True),
        sa.Column("hospital_id_hash", sa.String(64), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("block_hash", sa.String(64), nullable=False),
        sa.Column("ledger_timestamp", sa.String(50), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("block_index"),
    )
    # Grant only INSERT + SELECT to the application role (no UPDATE or DELETE)
    op.execute("GRANT SELECT, INSERT ON records_ledger_entries TO dhrs")
    op.execute("GRANT SELECT, INSERT ON trust_ledger_blocks TO dhrs")

    op.create_table(
        "appointments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("appointment_id", sa.String(100), nullable=False),
        sa.Column("doctor_id", sa.String(100), nullable=False),
        sa.Column("doctor_name", sa.String(200), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("appointment_id"),
    )


def downgrade() -> None:
    op.drop_table("appointments")
    op.drop_table("records_ledger_entries")
    op.drop_table("trust_ledger_blocks")
    op.drop_table("doctor_keys")
    op.drop_index("ix_medical_records_med_id", table_name="medical_records")
    op.drop_table("medical_records")
