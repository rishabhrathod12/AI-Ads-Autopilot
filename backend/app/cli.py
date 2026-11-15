"""Operator CLI. ``python -m app.cli seed-mock [--org-id UUID] [--seed N] [--days N]``."""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.assistant.action_guard import is_action_request
from app.application.knowledge.ingest import ingest_document
from app.application.llm.cost import Pricing
from app.application.llm.redact import redact
from app.application.ports.knowledge_store import SourceType
from app.application.ports.llm import LLMClient, LLMError, Message
from app.application.ports.member_store import AlreadyMemberError
from app.application.sync_service import SyncService
from app.config import Settings
from app.domain.auth import Role
from app.domain.tenant import TenantContext
from app.infrastructure.db.ads_store import SqlAdsStore
from app.infrastructure.db.job_runner import JOBS, run_jobs
from app.infrastructure.db.knowledge_store import SqlKnowledgeStore
from app.infrastructure.db.member_store import SqlMemberStore
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


async def bootstrap_owner(settings: Settings, email: str, org_id: UUID, org_name: str) -> str:
    """Create the organization (if new) and invite its first owner. Idempotent.

    Sign-in is by invitation, so someone must be invited before anyone can log in. The owner's
    first login with a provider-verified email links them (ADR 0026).
    """
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not set")
    engine = create_engine(settings.database_url.get_secret_value())
    try:
        async with tenant_session(engine, TenantContext(org_id), settings.db_app_role) as session:
            if (
                await session.scalar(select(Organization.id).where(Organization.id == org_id))
                is None
            ):
                session.add(Organization(id=org_id, name=org_name))
                await session.flush()
            try:
                await SqlMemberStore(session, org_id).invite(email, Role.OWNER)
            except AlreadyMemberError:
                return f"{email} is already a member of {org_id}"
        return f"Invited {email} as owner of {org_id}; they can now sign in."
    finally:
        await engine.dispose()


async def run_scheduled_jobs(
    settings: Settings, jobs: list[str], org_ids: list[UUID] | None, force: bool
) -> int:
    """Run maintenance jobs for every organization (or the given ones). Exit 1 if any failed."""
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not set")
    engine = create_engine(settings.database_url.get_secret_value())
    try:
        outcomes = await run_jobs(
            engine, settings.db_app_role, jobs, datetime.now(UTC), org_ids=org_ids, force=force
        )
    finally:
        await engine.dispose()
    for o in outcomes:
        print(f"{o.status:8} {o.job:9} {o.organization_id}  {o.detail}")
    failed = sum(o.status == "failed" for o in outcomes)
    print(f"{len(outcomes)} job run(s), {failed} failed")
    return 1 if failed else 0


def print_guard_report(lines: list[str]) -> int:
    """Print FLAGGED or MISSED for each non-empty line, then a summary. Exit 0 always.

    Meant for checking wordings of *requests to change things*: a line the guard does not flag
    is a request it would not add the "I can't make changes" notice to.
    """
    questions = [ln.strip() for ln in lines if ln.strip()]
    flagged = 0
    for text in questions:
        hit = is_action_request(text)
        flagged += hit
        print(f"{'FLAGGED' if hit else 'MISSED ':7}  {text}")
    print(f"\n{flagged} of {len(questions)} flagged; {len(questions) - flagged} not flagged")
    return 0


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
    boot = sub.add_parser("bootstrap-owner", help="invite the first owner of an organization")
    boot.add_argument("--email", required=True)
    boot.add_argument("--org-id", type=UUID, default=None, help="existing org; default: a new one")
    boot.add_argument("--org-name", default="My Organization")
    jobs = sub.add_parser(
        "run-jobs", help="run scheduled maintenance (expire recommendations, refresh forecasts)"
    )
    jobs.add_argument("--jobs", nargs="+", choices=JOBS, default=list(JOBS))
    jobs.add_argument("--org-id", type=UUID, action="append", help="only this org (repeatable)")
    jobs.add_argument("--force", action="store_true", help="refresh forecasts even if recent")
    guard = sub.add_parser(
        "check-guard",
        help="offline: which wordings does the assistant's action-request guard flag? (no model)",
    )
    guard.add_argument(
        "questions", nargs="*", help="wordings to check; none = read lines from stdin"
    )
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
    if args.command == "check-guard":
        lines = args.questions or [ln.rstrip("\n") for ln in sys.stdin]
        raise SystemExit(print_guard_report(lines))
    if args.command == "run-jobs":
        raise SystemExit(
            asyncio.run(run_scheduled_jobs(settings, args.jobs, args.org_id, args.force))
        )
    if args.command == "bootstrap-owner":
        print(
            asyncio.run(
                bootstrap_owner(settings, args.email, args.org_id or uuid4(), args.org_name)
            )
        )
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
