from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status

from app.storage.redis_store import get_redis, get_session


async def require_active_session(
    session_id: str,
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, Any]:
    """
    FastAPI dependency. Verifies the session exists in Redis with ACTIVE status.

    FastAPI automatically injects `session_id` from the matching path parameter
    `{session_id}` on the route where this dependency is applied.

    Returns the full session data dict on success.
    """
    session_data = await get_session(redis, session_id)
    if session_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    if session_data.get("status") != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Session not active (status: {session_data.get('status')})",
        )
    return session_data
