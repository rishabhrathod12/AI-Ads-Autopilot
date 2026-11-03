"""Alembic environment (async). URL comes from DATABASE_URL via Settings, never from the ini."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from app.config import Settings
from app.infrastructure.db import (  # noqa: F401  (register tables on Base.metadata)
    ads_models,
    assistant_models,
    forecast_models,
    knowledge_models,
)
from app.infrastructure.db.models import Base
from app.infrastructure.db.session import create_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    override = config.attributes.get("url")
    if isinstance(override, str):
        return override
    url = Settings().database_url
    if url is None:
        raise RuntimeError("DATABASE_URL is not set")
    return url.get_secret_value()


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported")
asyncio.run(_run_online())
