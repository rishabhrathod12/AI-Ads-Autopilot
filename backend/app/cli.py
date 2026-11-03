"""Operator CLI. ``python -m app.cli seed-mock [--org-id UUID] [--seed N] [--days N]``."""

import argparse
import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.knowledge.ingest import ingest_document
from app.application.llm.cost import Pricing
from app.application.llm.redact import redact
from app.application.ports.knowledge_store import SourceType
from app.application.ports.llm import LLMClient, LLMError, Message
from app.application.sync_service import SyncService
from app.config import Settings
from app.domain.tenant import TenantContext
from app.infrastructure.db.ads_store import SqlAdsStore
from app.infrastructure.db.knowledge_store import SqlKnowledgeStore
from app.infrastructure.db.models import Organization
from app.infrastructure.db.session import create_engine, tenant_session
from app.infrastructure.llm.factory import build_ingest_embedder, build_resilient_client, pricing_of
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


async def llm_check(client: LLMClient, pricing: Pricing) -> list[str]:
    """One tiny request. Proves the key, model and price settings work end to end."""
    reply = await client.complete(
        [Message("user", "Reply with the single word: OK")], max_output_tokens=256, temperature=None
    )
    cost = pricing.cost(reply.usage)
    return [
        f"model: {reply.model}",
        f"finish_reason: {reply.finish_reason}",
        f"tokens: {reply.usage.input_tokens} in, {reply.usage.output_tokens} out",
        f"estimated cost: ${cost:.6f}",
        f"reply: {redact((reply.message.content or '').strip())[:80]!r}",
    ]


KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"


def knowledge_documents(directory: Path) -> list[tuple[str, str, SourceType, str]]:
    """(source_key, title, source_type, text) for every markdown file, in a stable order."""
    docs: list[tuple[str, str, SourceType, str]] = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        first = next((ln for ln in text.splitlines() if ln.startswith("# ")), None)
        title = first[2:].strip() if first else path.stem.replace("_", " ")
        kind: SourceType = "glossary" if "glossary" in path.stem else "guideline"
        docs.append((f"kb/{path.stem}", title, kind, text))
    return docs


async def ingest_knowledge(settings: Settings, directory: Path) -> list[str]:
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not set")
    docs = knowledge_documents(directory)
    if not docs:
        raise SystemExit(f"no .md files in {directory}")
    embedder = build_ingest_embedder(settings)
    engine = create_engine(settings.database_url.get_secret_value())
    lines: list[str] = []
    try:
        # Owner connection, deliberately: only the owner may write global documents.
        async with AsyncSession(engine) as session, session.begin():
            store = SqlKnowledgeStore(session)
            for key, title, kind, text in docs:
                r = await ingest_document(
                    store, embedder, source_key=key, title=title, source_type=kind, text=text
                )
                lines.append(
                    f"{r.status}: {r.source_key} ({r.chunks} chunks, {r.embedding_tokens} tokens)"
                )
        return lines
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
    sub.add_parser("llm-check", help="make one tiny real request to verify the LLM configuration")
    ingest = sub.add_parser(
        "ingest-knowledge", help="chunk, embed and store the reference documents (idempotent)"
    )
    ingest.add_argument("--dir", type=Path, default=KNOWLEDGE_DIR)
    args = parser.parse_args()

    settings = Settings()
    if args.command == "ingest-knowledge":
        try:
            for line in asyncio.run(ingest_knowledge(settings, args.dir)):
                print(line)
        except LLMError as exc:
            raise SystemExit(f"ingest-knowledge failed: {type(exc).__name__}: {exc}") from None
        return
    if args.command == "llm-check":
        try:
            lines = asyncio.run(llm_check(build_resilient_client(settings), pricing_of(settings)))
        except LLMError as exc:
            raise SystemExit(f"llm-check failed: {type(exc).__name__}: {exc}") from None
        for line in lines:
            print(line)
        return
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
