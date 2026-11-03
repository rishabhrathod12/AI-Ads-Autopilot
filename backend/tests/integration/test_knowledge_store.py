"""Knowledge base on PostgreSQL + pgvector: round trip, ranking parity, tenant isolation."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.application.knowledge.ingest import ingest_document
from app.application.ports.embedding import EMBEDDING_DIMENSIONS
from app.application.ports.knowledge_store import NewChunk, NewDocument
from app.domain.tenant import TenantContext
from app.infrastructure.db import knowledge_models  # noqa: F401
from app.infrastructure.db.knowledge_store import SqlKnowledgeStore
from app.infrastructure.db.models import Organization
from app.infrastructure.db.session import tenant_session
from app.infrastructure.llm.hash_embedder import HashingEmbedder
from tests.unit.memory_knowledge_store import MemoryKnowledgeStore

pytestmark = pytest.mark.db
ROLE = "adpilot_app"
EMB = HashingEmbedder()

GLOBAL_DOC = (
    "# Glossary\n\n## ACOS\n\nACOS is ad spend divided by ad sales.\n\n"
    "## ROAS\n\nROAS is ad sales divided by ad spend.\n\n"
    "## CTR\n\nCTR is clicks divided by impressions.\n"
)


async def new_org(engine: AsyncEngine) -> UUID:
    org = uuid4()
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        s.add(Organization(id=org, name="Knowledge test"))
    return org


async def ingest_as_owner(
    engine: AsyncEngine, key: str, text_: str, org: UUID | None = None
) -> str:
    async with AsyncSession(engine) as session, session.begin():
        result = await ingest_document(
            SqlKnowledgeStore(session),
            EMB,
            source_key=key,
            title=key,
            source_type="guideline",
            text=text_,
            organization_id=org,
        )
    return result.status


async def search_as(engine: AsyncEngine, org: UUID, question: str, limit: int = 5) -> list[str]:
    q = (await EMB.embed([question])).vectors[0]
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        passages = await SqlKnowledgeStore(s, org).search(q, EMB.model, limit)
    return [f"{p.source_key}|{p.heading}" for p in passages]


async def test_the_vector_extension_and_column_exist(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as s:
        ext = await s.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
        col = await s.scalar(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = 'knowledge_chunks'::regclass AND attname = 'embedding'"
            )
        )
    assert ext
    assert col == f"vector({EMBEDDING_DIMENSIONS})"


async def test_a_global_document_round_trips_and_is_searchable_by_any_tenant(
    engine: AsyncEngine,
) -> None:
    key = f"kb/{uuid4().hex[:8]}"
    assert await ingest_as_owner(engine, key, GLOBAL_DOC) == "created"
    org = await new_org(engine)
    top = await search_as(engine, org, "what is ROAS", 3)
    assert top[0].endswith("|Glossary > ROAS")  # any copy: identical text scores the same
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        docs = {d.source_key: d for d in await SqlKnowledgeStore(s, org).documents()}
    assert docs[key].scope == "global" and docs[key].chunks == 3


async def test_ingestion_is_idempotent_and_replacing_leaves_no_orphans(
    engine: AsyncEngine,
) -> None:
    key = f"kb/{uuid4().hex[:8]}"
    assert await ingest_as_owner(engine, key, GLOBAL_DOC) == "created"
    assert await ingest_as_owner(engine, key, GLOBAL_DOC) == "unchanged"
    assert await ingest_as_owner(
        engine, key, GLOBAL_DOC + "\n## CPC\n\nCPC is spend per click.\n"
    ) == ("replaced")
    async with AsyncSession(engine) as s:
        chunks = await s.scalar(
            text(
                "SELECT count(*) FROM knowledge_chunks c JOIN knowledge_documents d "
                "ON d.id = c.document_id WHERE d.source_key = :k"
            ),
            {"k": key},
        )
        orphans = await s.scalar(
            text(
                "SELECT count(*) FROM knowledge_chunks c "
                "LEFT JOIN knowledge_documents d ON d.id = c.document_id WHERE d.id IS NULL"
            )
        )
    assert chunks == 4 and orphans == 0


async def test_sql_ranking_matches_the_in_memory_reference(engine: AsyncEngine) -> None:
    key = f"kb/{uuid4().hex[:8]}"
    await ingest_as_owner(engine, key, GLOBAL_DOC)
    memory = MemoryKnowledgeStore()
    await ingest_document(
        memory, EMB, source_key=key, title=key, source_type="guideline", text=GLOBAL_DOC
    )
    org = await new_org(engine)
    for question in ("what is ACOS", "clicks divided by impressions", "ad sales over spend"):
        q = (await EMB.embed([question])).vectors[0]
        want = [p.heading for p in await memory.search(q, EMB.model, 3)]
        got = [h.split("|")[1] for h in await search_as(engine, org, question, 50) if key in h][:3]
        assert got == want, question


async def test_scores_agree_with_the_in_memory_cosine(engine: AsyncEngine) -> None:
    key = f"kb/{uuid4().hex[:8]}"
    await ingest_as_owner(engine, key, GLOBAL_DOC)
    memory = MemoryKnowledgeStore()
    await ingest_document(
        memory, EMB, source_key=key, title=key, source_type="guideline", text=GLOBAL_DOC
    )
    q = (await EMB.embed(["what is ACOS"])).vectors[0]
    org = await new_org(engine)
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        sql = {p.heading: p.score for p in await SqlKnowledgeStore(s, org).search(q, EMB.model, 50)}
    for p in await memory.search(q, EMB.model, 10):
        assert sql[p.heading] == pytest.approx(p.score, abs=1e-5)


async def test_a_tenants_notes_are_invisible_to_other_tenants(engine: AsyncEngine) -> None:
    mine, theirs = await new_org(engine), await new_org(engine)
    marker = f"zebra{uuid4().hex[:6]}"
    note = f"# Notes\n\n## Launch\n\nThe {marker} yoga mats launch in spring.\n"
    await ingest_as_owner(engine, "notes/launch", note, mine)
    found_by_owner = await search_as(engine, mine, f"{marker} yoga mats", 1)
    found_by_other = await search_as(engine, theirs, f"{marker} yoga mats", 5)
    assert found_by_owner == ["notes/launch|Notes > Launch"]
    assert "notes/launch|Notes > Launch" not in found_by_other
    async with tenant_session(engine, TenantContext(theirs), ROLE) as s:
        leaked = await s.scalar(
            text("SELECT count(*) FROM knowledge_documents WHERE organization_id = :o"),
            {"o": mine},
        )
        # Even an explicit request for the other tenant's chunks is filtered by the database.
        leaked_chunks = await s.scalar(
            text("SELECT count(*) FROM knowledge_chunks WHERE organization_id = :o"), {"o": mine}
        )
    assert (leaked, leaked_chunks) == (0, 0)


async def test_the_application_role_cannot_write_or_alter_knowledge(engine: AsyncEngine) -> None:
    org = await new_org(engine)
    doc = NewDocument("kb/forged", "Forged", "glossary", "0" * 64, None)
    chunk = NewChunk(0, "h", "c", [0.0] * EMBEDDING_DIMENSIONS, "m")
    with pytest.raises((ProgrammingError, DBAPIError)):
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            await SqlKnowledgeStore(s, org).replace_document(doc, [chunk])
    with pytest.raises((ProgrammingError, DBAPIError)):
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            await s.execute(text("DELETE FROM knowledge_documents"))
    with pytest.raises((ProgrammingError, DBAPIError)):
        async with tenant_session(engine, TenantContext(org), ROLE) as s:
            await s.execute(text("UPDATE knowledge_chunks SET content = 'tampered'"))


async def test_no_tenant_context_sees_only_global_documents(engine: AsyncEngine) -> None:
    mine = await new_org(engine)
    await ingest_as_owner(engine, "notes/private", "# Notes\n\n## A\n\nPrivate note text.\n", mine)
    async with tenant_session(engine, TenantContext(uuid4()), ROLE) as s:
        scopes = {d.scope for d in await SqlKnowledgeStore(s, None).documents()}
    assert scopes <= {"global"}


async def test_chunks_of_other_embedding_models_are_not_searched(engine: AsyncEngine) -> None:
    key = f"kb/{uuid4().hex[:8]}"
    await ingest_as_owner(engine, key, GLOBAL_DOC)
    org = await new_org(engine)
    q = (await EMB.embed(["what is ACOS"])).vectors[0]
    async with tenant_session(engine, TenantContext(org), ROLE) as s:
        assert await SqlKnowledgeStore(s, org).search(q, "some-other-model", 5) == []
