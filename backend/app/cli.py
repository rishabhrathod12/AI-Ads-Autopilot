"""Operator CLI. ``python -m app.cli seed-mock [--org-id UUID] [--seed N] [--days N]``."""

import argparse
import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.application.sync_service import SyncService
from app.config import Settings
from app.domain.tenant import TenantContext
from app.infrastructure.db.ads_store import SqlAdsStore
from app.infrastructure.db.models import Organization
from app.infrastructure.db.session import create_engine, tenant_session
from app.infrastructure.providers.mock.generator import MockConfig
from app.infrastructure.providers.mock.provider import MockAdvertisingDataProvider


async def seed_mock(settings: Settings, org_id: UUID, org_name: str) -> None:
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not set")
    if settings.ads_provider != "mock":
        raise SystemExit("seed-mock requires ADS_PROVIDER=mock")
    config = MockConfig(seed=settings.mock_seed, days=settings.mock_history_days)
    provider = MockAdvertisingDataProvider(config)
    start = config.end_date - timedelta(days=config.days - 1)
    engine = create_engine(settings.database_url.get_secret_value())
    try:
        async with tenant_session(engine, TenantContext(org_id), settings.db_app_role) as session:
            if (
                await session.scalar(select(Organization.id).where(Organization.id == org_id))
                is None
            ):
                session.add(Organization(id=org_id, name=org_name))
                await session.flush()
            result = await SyncService(provider, SqlAdsStore(session, org_id)).sync(
                start, config.end_date
            )
        print(
            f"Seeded MOCK data for org {org_id}: {result.campaigns} campaigns, "
            f"{result.targets} targets, {result.search_terms} search terms, "
            f"{result.metrics_rows} metric rows ({result.rows_quarantined} quarantined)"
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="adpilot")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed-mock", help="load synthetic MOCK data for a demo organization")
    seed.add_argument("--org-id", type=UUID, default=None, help="reuse an org (idempotent re-seed)")
    seed.add_argument("--org-name", default="Demo Organization (MOCK)")
    seed.add_argument("--seed", type=int, default=None)
    seed.add_argument("--days", type=int, default=None)
    args = parser.parse_args()

    settings = Settings()
    updates: dict[str, int] = {}
    if args.seed is not None:
        updates["mock_seed"] = args.seed
    if args.days is not None:
        updates["mock_history_days"] = args.days
    settings = settings.model_copy(update=updates)
    org_id = args.org_id or uuid4()
    asyncio.run(seed_mock(settings, org_id, args.org_name))


if __name__ == "__main__":
    main()
