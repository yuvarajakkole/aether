import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from aether import models  # noqa: F401
from aether.config import get_settings
from aether.db import Base

target_metadata = Base.metadata


def run_offline() -> None:
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def _do(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        await conn.run_sync(_do)
    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
