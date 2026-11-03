# ADR 0017: Reference retrieval: pgvector, exact search, our own documents

Status: Accepted (M4 step 5)

## Context
The assistant must answer "what is ACOS" or "when should I add a negative keyword" without inventing definitions, and
must keep reference guidance visibly separate from the seller's own numbers. The corpus is small (tens of sections),
the deployment already runs PostgreSQL, and the master context forbids copying Amazon documentation and forbids
treating retrieved text as instructions.

## Decision
- **Store:** PostgreSQL with the `pgvector` extension (`knowledge_documents`, `knowledge_chunks`, `vector(1536)`). No
  separate vector database.
- **Exact cosine search, no ANN index.** At hundreds of chunks a sequential scan is fast and returns the true nearest
  neighbours; HNSW/IVFFlat would add approximate results, tuning and rebuilds for no benefit. Revisit above roughly
  100k chunks.
- **Embedding behind a port** (`EmbeddingClient`): `HashingEmbedder` (offline, deterministic, used in tests and
  demos; not semantic), `OpenAIEmbeddings` (verified only against a mocked transport so far), both wrapped with
  retries and a per-tenant spend cap that shares the LLM ledger. Every chunk records its `embedding_model`; searches
  only compare vectors from the same model, so switching models can never silently mix spaces. Changing the model
  re-embeds on the next ingest.
- **Content:** the documents under `backend/knowledge/` are written by the AdPilot team, consistent with
  `thresholds.py`. No Amazon text is copied. Statements about Amazon's own rules are deliberately general and hedged.
- **Ingestion:** `python -m app.cli ingest-knowledge`, idempotent by SHA-256 of the text plus the embedding model;
  an unchanged document costs nothing. Chunks follow markdown headings and are embedded with their heading path.
- **Tenancy:** `organization_id IS NULL` means a global document. Row-level security is `ENABLE` (not `FORCE`) with a
  read-only policy "global or own tenant"; the application role has `SELECT` only. Global documents are therefore
  writable only by the table owner (the CLI), and a tenant can never read another tenant's notes, even by asking
  for them explicitly.
- **Use in answers:** one read-only tool, `search_knowledge`, offered only when a knowledge base and embedder are
  configured. Results are evidence like any other (cited by id, with title, section and scope), flagged and capped as
  untrusted data, and the system prompt says they are reference material, not the seller's figures. Numbers quoted
  from a cited passage are accepted by the faithfulness validator without unit matching (prose does not say which
  field a number belongs to); they never vouch for an uncited claim and cannot stand in for account data.

## Consequences
- Retrieval quality with `HashingEmbedder` is lexical. The tests prove plumbing and ranking mechanics, not semantic
  quality; judging answers needs the real model in step 7, and I will say so in the results.
- Org-owned notes can be read and searched now, but a path for tenants to upload them is not built (the application
  role cannot write). That is intentional until authentication and moderation exist.
- Prompt-injection defence is layered (sanitising, flagging, prompt rule, read-only tools, validator), not absolute.
  The tools cannot change anything, which bounds the damage.
