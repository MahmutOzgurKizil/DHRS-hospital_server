from __future__ import annotations

import base64
import json
import os
import struct
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.redis_store import get_session, set_session


# ── Crypto helpers (same as test_decryption_engine.py) ───────────────────────

def _gen_rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _encrypt_pinfo(pub_key, pinfo: dict) -> str:
    aes_key = os.urandom(32)
    nonce = os.urandom(12)
    ct = AESGCM(aes_key).encrypt(nonce, json.dumps(pinfo).encode(), None)
    rsa_ct = pub_key.encrypt(
        aes_key,
        asym_padding.OAEP(mgf=asym_padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    wire = struct.pack(">H", len(rsa_ct)) + rsa_ct + nonce + ct
    return base64.b64encode(wire).decode()


def _encrypt_med_id(pub_key, med_id: str) -> str:
    ct = pub_key.encrypt(
        med_id.encode(),
        asym_padding.OAEP(mgf=asym_padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode()


def _encrypt_dsk(priv_key, kek: bytes) -> bytes:
    pem = priv_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    nonce = os.urandom(12)
    return nonce + AESGCM(kek).encrypt(nonce, pem, None)


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_create_session_returns_session_id_and_qr(client: AsyncClient) -> None:
    resp = await client.post(
        "/sessions", json={"appointment_id": "appt-1", "doctor_id": "dr-1"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert "qr_payload" in data
    assert "expires_at" in data


async def test_create_session_sets_redis_pending(client: AsyncClient, fake_redis) -> None:
    resp = await client.post(
        "/sessions", json={"appointment_id": "appt-1", "doctor_id": "dr-1"}
    )
    session_id = resp.json()["session_id"]
    session_data = await get_session(fake_redis, session_id)
    assert session_data is not None
    assert session_data["status"] == "PENDING"
    assert session_data["doctor_id"] == "dr-1"


async def test_authorize_requires_pending_session(client: AsyncClient) -> None:
    resp = await client.post(
        "/sessions/authorize",
        json={
            "session_id": str(uuid.uuid4()),
            "enc_pinfo": base64.b64encode(b"x").decode(),
            "enc_med_id": base64.b64encode(b"x").decode(),
        },
    )
    assert resp.status_code == 400


async def test_full_authorize_flow(
    client: AsyncClient,
    fake_redis,
    db_session: AsyncSession,
) -> None:
    kek = bytes.fromhex("ab" * 32)
    hsk_key = _gen_rsa_key()
    doctor_key = _gen_rsa_key()
    pinfo = {"name": "Test Patient", "dob": "1990-01-01"}
    med_id = str(uuid.uuid4())

    enc_pinfo_b64 = _encrypt_pinfo(hsk_key.public_key(), pinfo)
    enc_med_id_b64 = _encrypt_med_id(doctor_key.public_key(), med_id)
    encrypted_dsk = _encrypt_dsk(doctor_key, kek)

    # Seed doctor key into the DB
    from app.models.doctor_key import DoctorKey
    from app.models.base import new_uuid
    db_session.add(
        DoctorKey(
            doctor_id="dr-1",
            doctor_name="Dr. One",
            encrypted_dsk=encrypted_dsk,
            public_key_pem=doctor_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode(),
            is_active=True,
        )
    )
    await db_session.commit()

    # Create session
    create_resp = await client.post(
        "/sessions", json={"appointment_id": "appt-1", "doctor_id": "dr-1"}
    )
    session_id = create_resp.json()["session_id"]

    # Set up a mock DecryptionEngine that uses our test keys
    from app.modules.decryption_engine import DecryptionEngine
    from app.modules import get_decryption_engine

    engine = DecryptionEngine()
    object.__setattr__(engine, "_hsk", hsk_key)

    import app.config as app_config
    original_kek = app_config.settings.kek_hex
    app_config.settings.kek_hex = "ab" * 32

    from app.main import app as fastapi_app
    fastapi_app.dependency_overrides[get_decryption_engine] = lambda: engine

    try:
        auth_resp = await client.post(
            "/sessions/authorize",
            json={
                "session_id": session_id,
                "enc_pinfo": enc_pinfo_b64,
                "enc_med_id": enc_med_id_b64,
            },
        )
        assert auth_resp.status_code == 200, auth_resp.text
        data = auth_resp.json()
        assert "temp_id" in data
        assert data["status"] == "ACTIVE"

        # Redis status should now be ACTIVE
        session_data = await get_session(fake_redis, session_id)
        assert session_data["status"] == "ACTIVE"
    finally:
        app_config.settings.kek_hex = original_kek
        fastapi_app.dependency_overrides.clear()
        # Re-apply the standard test overrides
        from app.storage.database import get_db
        from app.storage.redis_store import get_redis
        from app.middleware.mtls import verify_mtls
        fastapi_app.dependency_overrides[get_db] = lambda: db_session
        fastapi_app.dependency_overrides[get_redis] = lambda: fake_redis
        fastapi_app.dependency_overrides[verify_mtls] = lambda: "test-hospital"


async def test_get_data_requires_active_session(client: AsyncClient) -> None:
    session_id = str(uuid.uuid4())
    resp = await client.get(
        f"/sessions/{session_id}/data",
        headers={"X-Temp-Id": "some-temp-id"},
    )
    assert resp.status_code == 404


async def test_get_data_wrong_temp_id_returns_403(
    client: AsyncClient,
    fake_redis,
    db_session: AsyncSession,
) -> None:
    from app.storage.medical_id_table import medical_id_table, MedicalIDEntry
    from datetime import timedelta, timezone
    from datetime import datetime

    session_id = str(uuid.uuid4())
    await set_session(fake_redis, session_id, {"status": "ACTIVE", "doctor_id": "dr-1"}, 3600)
    await medical_id_table.insert(
        session_id,
        MedicalIDEntry(
            temp_id="correct-temp-id",
            med_id=str(uuid.uuid4()),
            pinfo={"name": "Alice"},
            expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
        ),
    )
    resp = await client.get(
        f"/sessions/{session_id}/data",
        headers={"X-Temp-Id": "wrong-temp-id"},
    )
    assert resp.status_code == 403
    await medical_id_table.delete(session_id)


async def test_delete_session_clears_state(
    client: AsyncClient,
    fake_redis,
    db_session: AsyncSession,
) -> None:
    from app.storage.medical_id_table import medical_id_table, MedicalIDEntry
    from datetime import datetime, timedelta, timezone

    session_id = str(uuid.uuid4())
    await set_session(fake_redis, session_id, {"status": "ACTIVE", "doctor_id": "dr-1"}, 3600)
    await medical_id_table.insert(
        session_id,
        MedicalIDEntry(
            temp_id="temp-x",
            med_id=str(uuid.uuid4()),
            pinfo={"name": "Bob"},
            expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
        ),
    )

    resp = await client.delete(f"/sessions/{session_id}")
    assert resp.status_code == 204

    # Memory and Redis should both be cleared
    assert await medical_id_table.get_by_session(session_id) is None
    assert await get_session(fake_redis, session_id) is None


async def test_temp_id_not_in_any_url(client: AsyncClient) -> None:
    """TempID must only appear in headers, never in URLs."""
    from app.main import app as fastapi_app
    for route in fastapi_app.routes:
        path = getattr(route, "path", "")
        assert "temp_id" not in path.lower(), f"temp_id found in route path: {path}"
        assert "tempid" not in path.lower(), f"tempid found in route path: {path}"


async def test_health_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
