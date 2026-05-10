from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.config import settings
from app.storage.database import engine
from app.storage.medical_id_table import medical_id_table
from app.storage.redis_store import close_redis

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logging.basicConfig(level=settings.log_level)
    logger.info("Hospital Server starting — hospital_id=%s", settings.hospital_id)

    # Background task: purge expired Medical ID Table entries every 60 seconds
    purge_task = asyncio.create_task(_purge_loop())

    yield

    purge_task.cancel()
    try:
        await purge_task
    except asyncio.CancelledError:
        pass

    await close_redis()
    await engine.dispose()
    logger.info("Hospital Server stopped")


async def _purge_loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            count = await medical_id_table.purge_expired()
            if count:
                logger.info("Purged %d expired Medical ID Table entries", count)
        except Exception:
            logger.exception("Error during Medical ID Table purge")


app = FastAPI(
    title="DHRS Hospital Server",
    description="Hospital backend for the Distributed Hospital Appointment System",
    version="1.0.0",
    lifespan=lifespan,
    # Disable OpenAPI in production by setting openapi_url=None via env
)

from app.routers import access_log, appointments, cross_hospital, sessions, trust  # noqa: E402

app.include_router(sessions.router)
app.include_router(appointments.router)
app.include_router(cross_hospital.router)
app.include_router(trust.router)
app.include_router(access_log.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "hospital_id": settings.hospital_id}
