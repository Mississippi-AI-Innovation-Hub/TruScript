"""
Async SQLAlchemy engine and session factory.

`get_async_session` is the FastAPI dependency used in all request handlers.
The session is committed on success and rolled back on any exception.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def _json_serializer(obj: object) -> str:
    """
    Custom JSON serializer for SQLAlchemy JSONB columns.

    Python's default json.dumps raises TypeError on datetime objects.
    SQLAlchemy passes JSONB column values through json.dumps before sending
    them to the database driver, so any datetime inside a JSONB value (e.g.
    StaffAnnotation.created_at) would crash with a 500.

    This serializer converts datetime/date → ISO-8601 string so JSONB
    storage always succeeds.
    """
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.debug,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_timeout=_settings.db_pool_timeout,
    pool_pre_ping=True,
    json_serializer=lambda obj: json.dumps(obj, default=_json_serializer),
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a transactional async DB session; auto-commits or rolls back."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
