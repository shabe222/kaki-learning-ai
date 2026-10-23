"""DB URL + FastAPI 数据库依赖"""
from sqlalchemy.ext.asyncio import AsyncSession

import database.connection as db_conn
from config import settings


def get_build_db_url() -> str:
    """根据 DB_ENGINE 构建 PostgreSQL 连接字符串"""
    if settings.DB_ENGINE == "postgresql":
        return (
            f"postgresql+asyncpg://{settings.PG_USER}:{settings.PG_PASSWORD}"
            f"@{settings.PG_HOST}:{settings.PG_PORT}/{settings.PG_DATABASE}"
        )
    return settings.DATABASE_URL


async def get_db() -> AsyncSession:
    if db_conn._async_session_factory is None:
        raise RuntimeError("数据库未初始化")
    async with db_conn._async_session_factory() as session:
        yield session
