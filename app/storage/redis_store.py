from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from app.config import settings

_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


async def set_session(
    redis: aioredis.Redis,
    session_id: str,
    data: dict[str, Any],
    ttl: int,
) -> None:
    key = f"session:{session_id}"
    await redis.set(key, json.dumps(data), ex=ttl)


async def get_session(
    redis: aioredis.Redis,
    session_id: str,
) -> dict[str, Any] | None:
    key = f"session:{session_id}"
    raw = await redis.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def update_session_status(
    redis: aioredis.Redis,
    session_id: str,
    status: str,
) -> None:
    key = f"session:{session_id}"
    ttl = await redis.ttl(key)
    raw = await redis.get(key)
    if raw is None:
        return
    data = json.loads(raw)
    data["status"] = status
    effective_ttl = ttl if ttl > 0 else settings.session_ttl_seconds
    await redis.set(key, json.dumps(data), ex=effective_ttl)


async def delete_session(redis: aioredis.Redis, session_id: str) -> None:
    await redis.delete(f"session:{session_id}")
