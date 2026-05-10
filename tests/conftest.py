"""
Test configuration.

Environment variables are set BEFORE any app module is imported so that
pydantic-settings picks them up at Settings() instantiation time.
"""
from __future__ import annotations

import os

# Must be set before any app import
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("HSK_PEM_PATH", "/dev/null")
os.environ.setdefault("KEK_HEX", "ab" * 32)
os.environ.setdefault("HOSPITAL_ID", "test-hospital")
os.environ.setdefault("MTLS_CA_CERT_PATH", "/dev/null")

import pytest
import pytest_asyncio
import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.storage.medical_id_table import MedicalIDTable


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def fake_redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
def test_table():
    return MedicalIDTable()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, fake_redis):
    from app.main import app
    from app.middleware.mtls import verify_mtls
    from app.modules import (
        get_cross_hospital,
        get_data_write,
        get_decryption_engine,
        get_ledger,
        get_retrieval,
        get_session_mapping,
        get_termination,
    )
    from app.storage.database import get_db
    from app.storage.redis_store import get_redis

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[verify_mtls] = lambda: "test-hospital"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
