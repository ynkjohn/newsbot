import asyncio
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_timeout=30,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session


def sqlalchemy_url_for_alembic(database_url: str) -> str:
    """Map async SQLAlchemy URL to sync URL for Alembic CLI."""
    if database_url.startswith("sqlite+aiosqlite://"):
        return "sqlite://" + database_url.removeprefix("sqlite+aiosqlite://")
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg2://" + database_url.removeprefix("postgresql+asyncpg://")
    return database_url


def _run_alembic_upgrade_sync() -> None:
    """Run `alembic upgrade head` with DB URL aligned to application settings."""
    root = Path(__file__).resolve().parent.parent
    sync_url = sqlalchemy_url_for_alembic(settings.database_url)
    cmd = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(root / "alembic.ini"),
        "-x",
        f"sqlalchemy.url={sync_url}",
        "upgrade",
        "head",
    ]
    subprocess.run(cmd, cwd=str(root), check=True)


async def init_db() -> None:
    """Create schema via SQLAlchemy, or apply Alembic migrations only.

    When ``NEWSBOT_USE_ALEMBIC_ONLY`` is truthy (e.g. Docker Compose for ``newsbot``),
    runs ``alembic upgrade head`` with a sync URL derived from ``DATABASE_URL`` and skips
    ``create_all``. Otherwise uses ``metadata.create_all`` (typical local dev).
    """
    from db.models import Base

    use_alembic_only = os.getenv("NEWSBOT_USE_ALEMBIC_ONLY", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if use_alembic_only:
        await asyncio.to_thread(_run_alembic_upgrade_sync)
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
