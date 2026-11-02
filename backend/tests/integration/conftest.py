"""Integration fixtures. DB tests need TEST_DATABASE_URL (postgresql+asyncpg://owner@host/db)."""

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

from app.infrastructure.db.session import create_engine

DB_URL = os.environ.get("TEST_DATABASE_URL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if DB_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL not set")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


def _alembic_cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = DB_URL
    return cfg


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[str]:
    assert DB_URL
    cfg = _alembic_cfg()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield DB_URL
    command.downgrade(cfg, "base")


@pytest.fixture
async def engine(migrated_db: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(migrated_db)
    yield eng
    await eng.dispose()
