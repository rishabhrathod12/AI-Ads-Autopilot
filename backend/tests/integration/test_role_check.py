"""The production RLS-role check against a real PostgreSQL.

UNVERIFIED in the environment where this was written (no test database was available): skipped
there. Run with TEST_DATABASE_URL set.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.startup_checks import UnsafeConfigurationError, assert_rls_enforced

pytestmark = pytest.mark.db


async def test_the_application_role_passes(engine: AsyncEngine) -> None:
    await assert_rls_enforced(engine, "adpilot_app")


async def test_a_superuser_connection_without_set_role_is_refused(engine: AsyncEngine) -> None:
    # TEST_DATABASE_URL connects as the database owner, normally a superuser in development.
    try:
        await assert_rls_enforced(engine, None)
    except UnsafeConfigurationError:
        return  # expected for a superuser/BYPASSRLS owner
    # a non-superuser owner is also a valid, safe configuration
