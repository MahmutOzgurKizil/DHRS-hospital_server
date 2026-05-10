from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=not _is_sqlite,
    **({} if _is_sqlite else {"pool_size": 10, "max_overflow": 20}),
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
