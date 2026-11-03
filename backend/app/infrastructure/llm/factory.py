"""Build the configured LLM stack: provider adapter -> retries/timeouts -> per-tenant spend cap."""

from uuid import UUID

from app.application.llm.budget import BudgetedLLMClient, SpendLedger, SpendLimits
from app.application.llm.cost import Pricing
from app.application.llm.embedding import BudgetedEmbeddingClient, ResilientEmbeddingClient
from app.application.llm.resilient import ResilientLLMClient
from app.application.ports.embedding import EmbeddingClient
from app.application.ports.llm import LLMClient, LLMNotConfiguredError
from app.config import Settings
from app.infrastructure.llm.hash_embedder import HashingEmbedder
from app.infrastructure.llm.openai_client import OpenAIClient
from app.infrastructure.llm.openai_embeddings import OpenAIEmbeddings


def pricing_of(settings: Settings) -> Pricing:
    if settings.llm_input_usd_per_million is None or settings.llm_output_usd_per_million is None:
        raise LLMNotConfiguredError("LLM prices are not configured")
    return Pricing(settings.llm_input_usd_per_million, settings.llm_output_usd_per_million)


def limits_of(settings: Settings) -> SpendLimits:
    return SpendLimits(settings.llm_daily_cap_usd, settings.llm_monthly_cap_usd)


def build_raw_client(settings: Settings) -> OpenAIClient:
    if settings.llm_provider != "openai":
        raise LLMNotConfiguredError("no LLM provider configured (LLM_PROVIDER=none)")
    if settings.openai_api_key is None or not settings.llm_model:
        raise LLMNotConfiguredError("OPENAI_API_KEY and LLM_MODEL are required")
    return OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.llm_model,
        base_url=settings.openai_base_url,
    )


def build_resilient_client(settings: Settings) -> LLMClient:
    return ResilientLLMClient(
        build_raw_client(settings),
        max_retries=settings.llm_max_retries,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def build_budgeted_client(
    settings: Settings, ledger: SpendLedger, organization_id: UUID
) -> LLMClient:
    """The client the assistant should use: every call is capped and metered per tenant."""
    return BudgetedLLMClient(
        build_resilient_client(settings),
        ledger,
        organization_id,
        limits_of(settings),
        pricing_of(settings),
    )


def build_embedder(
    settings: Settings, ledger: SpendLedger, organization_id: UUID
) -> EmbeddingClient:
    """The embedder the assistant uses to search knowledge: retried, and metered per tenant."""
    if settings.embedding_provider == "hashing":
        return HashingEmbedder()  # offline and free: nothing to meter
    if settings.embedding_provider != "openai":
        raise LLMNotConfiguredError("no embedding provider configured (EMBEDDING_PROVIDER=none)")
    if settings.openai_api_key is None or not settings.embedding_model:
        raise LLMNotConfiguredError("OPENAI_API_KEY and EMBEDDING_MODEL are required")
    if settings.embedding_usd_per_million is None:
        raise LLMNotConfiguredError("EMBEDDING_USD_PER_MILLION is required")
    raw = OpenAIEmbeddings(
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
        base_url=settings.openai_base_url,
    )
    resilient = ResilientEmbeddingClient(
        raw, max_retries=settings.llm_max_retries, timeout_seconds=settings.llm_timeout_seconds
    )
    return BudgetedEmbeddingClient(
        resilient, ledger, organization_id, limits_of(settings), settings.embedding_usd_per_million
    )


def build_ingest_embedder(settings: Settings) -> EmbeddingClient:
    """Embedder for the operator's ingest command: retried but not tenant-metered, because it
    embeds our own reference documents (a one-off, small, and the command prints its cost)."""
    if settings.embedding_provider == "hashing":
        return HashingEmbedder()
    if settings.embedding_provider != "openai":
        raise LLMNotConfiguredError("no embedding provider configured (EMBEDDING_PROVIDER=none)")
    if settings.openai_api_key is None or not settings.embedding_model:
        raise LLMNotConfiguredError("OPENAI_API_KEY and EMBEDDING_MODEL are required")
    raw = OpenAIEmbeddings(
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
        base_url=settings.openai_base_url,
    )
    return ResilientEmbeddingClient(
        raw, max_retries=settings.llm_max_retries, timeout_seconds=settings.llm_timeout_seconds
    )
