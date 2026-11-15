"""Retrieval quality of the knowledge base: does the right passage come back?

PRE-REGISTERED before the first run. Nothing below may be adjusted after seeing a result; a failed
criterion is recorded as a known failure in docs/ASSISTANT.md, not tuned away.

Setup: the two shipped documents (backend/knowledge/*.md) are chunked and embedded exactly as the
CLI does, into the in-memory store (same exact-cosine contract as the SQL store). 24 answerable
questions were written from the reader's side, not copied from the documents: 8 use the glossary's
own term ("What does ACOS mean?"), 16 paraphrase it without the key word ("What fraction of
shoppers who click end up buying?"). A question counts as retrieved when a passage whose heading
contains one of its accepted headings is among the top k. 6 unanswerable questions have no passage
that should be used.

Criteria (per embedder):
  A1  recall@3 over the 24 answerable questions >= 0.80
  A2  recall@1 over the 24 answerable questions >= 0.55
  A3  recall@3 over the 8 direct-term questions >= 0.90
Reported but NOT gated: mean reciprocal rank; the top-1 score of answerable versus unanswerable
questions and whether any single score cut-off separates them (it decides whether a minimum-score
filter is justified; none exists today).

Embedders: the offline hashing embedder (default, free). It is a hashed bag of words, NOT
semantic: it cannot match a paraphrase that shares no words, so its result describes the offline
stand-in only.
With RUN_LLM_EVAL=1 and a configured embedding model, the real embedder is scored on the same
questions (about 60 short embeddings, well under one cent).

Run: python -m pytest tests/evals/test_retrieval_eval.py -m eval -s
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.application.knowledge.ingest import ingest_document
from app.application.ports.embedding import EmbeddingClient
from app.cli import KNOWLEDGE_DIR, knowledge_documents
from app.infrastructure.llm.hash_embedder import HashingEmbedder
from tests.unit.memory_knowledge_store import MemoryKnowledgeStore

pytestmark = pytest.mark.eval
RESULTS = Path(__file__).parent / "results"


@dataclass(frozen=True)
class Q:
    text: str
    accept: tuple[str, ...]  # any passage heading containing one of these counts
    direct: bool = False  # uses the glossary's own term


ANSWERABLE = [
    Q("What does ACOS mean?", ("ACOS (advertising cost of sales)",), True),
    Q("How is ROAS calculated?", ("ROAS",), True),
    Q("What is break-even ACOS?", ("Break-even ACOS",), True),
    Q("What is CTR?", ("CTR",), True),
    Q("What does CPC stand for and how is it computed?", ("CPC",), True),
    Q("Define conversion rate", ("Conversion rate",), True),
    Q("What are broad, phrase and exact match types?", ("Keyword and match types",), True),
    Q("What is a search term versus a keyword?", ("Search term",), True),
    Q("How much do I pay on average each time a shopper clicks my ad?", ("CPC",)),
    Q("What fraction of shoppers who click end up buying?", ("Conversion rate",)),
    Q("At what margin do my ads stop making money?", ("Break-even ACOS",)),
    Q(
        "What is it called when a campaign keeps running out of daily spend?",
        ("Budget constrained",),
    ),
    Q("Why do my recent days show weak sales numbers?", ("Attribution window",)),
    Q(
        "Does the ad-attributed figure include organic purchases?",
        ("Attributed sales versus total sales",),
    ),
    Q(
        "When should I stop an ad from appearing for a certain shopper query?",
        ("Negative keyword", "When to consider a negative keyword"),
    ),
    Q(
        "How many clicks do I need before judging a keyword?",
        ("Wait for enough data", "Spend without orders"),
    ),
    Q("Should I change bids by a lot at once?", ("Moving bids",)),
    Q("When is it fine to give a campaign more daily money?", ("Budgets", "Budget constrained")),
    Q("What should I do when my cost of sales jumps?", ("Find which factor moved",)),
    Q("Is one strange day worth reacting to?", ("Sustained shifts",)),
    Q(
        "Can this tool tell me my profit?",
        ("What the system cannot know", "Attributed sales versus total sales"),
    ),
    Q("Are the numbers in the demo real?", ("Mock data",)),
    Q("Which period lengths should I compare for fairness?", ("Compare like with like",)),
    Q("A search term converts but is not targeted exactly yet: what then?", ("Opportunities",)),
]
UNANSWERABLE = [
    "What is the capital of France?",
    "How do I fix a leaking kitchen tap?",
    "Write a poem about the sea.",
    "What is Amazon's referral fee for electronics?",
    "How do I configure FBA inbound shipments?",
    "Which CPU is best for gaming?",
]


async def _store(embedder: EmbeddingClient) -> MemoryKnowledgeStore:
    store = MemoryKnowledgeStore()
    for key, title, kind, text in knowledge_documents(KNOWLEDGE_DIR):
        await ingest_document(
            store, embedder, source_key=key, title=title, source_type=kind, text=text
        )
    return store


async def _top(store: MemoryKnowledgeStore, embedder: EmbeddingClient, text: str, k: int = 3):  # type: ignore[no-untyped-def]
    vec = (await embedder.embed([text])).vectors[0]
    return await store.search(vec, embedder.model, k)


def _hit(passages, accept: tuple[str, ...]) -> int | None:  # type: ignore[no-untyped-def]
    for rank, p in enumerate(passages, start=1):
        if any(a in p.heading for a in accept):
            return rank
    return None


async def _score(name: str, embedder: EmbeddingClient) -> dict[str, object]:
    store = await _store(embedder)
    ranks: list[int | None] = []
    top1_answerable: list[float] = []
    rows = []
    for q in ANSWERABLE:
        got = await _top(store, embedder, q.text)
        rank = _hit(got, q.accept)
        ranks.append(rank)
        top1_answerable.append(got[0].score)
        rows.append(
            {
                "q": q.text,
                "direct": q.direct,
                "rank": rank,
                "top": got[0].heading,
                "score": round(got[0].score, 3),
            }
        )
    top1_unanswerable = [(await _top(store, embedder, t, 1))[0].score for t in UNANSWERABLE]
    n = len(ANSWERABLE)
    recall3 = sum(r is not None for r in ranks) / n
    recall1 = sum(r == 1 for r in ranks) / n
    direct = [r for r, q in zip(ranks, ANSWERABLE, strict=True) if q.direct]
    summary = {
        "embedder": name,
        "recall_at_3": round(recall3, 3),
        "recall_at_1": round(recall1, 3),
        "direct_recall_at_3": round(sum(r is not None for r in direct) / len(direct), 3),
        "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
        "answerable_top1_score_min": round(min(top1_answerable), 3),
        "unanswerable_top1_score_max": round(max(top1_unanswerable), 3),
        "a_cutoff_separates_them": min(top1_answerable) > max(top1_unanswerable),
        "cases": rows,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"retrieval_{name}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print({k: v for k, v in summary.items() if k != "cases"})
    return summary


def _check(s: dict[str, object]) -> None:
    assert s["recall_at_3"] >= 0.80, f"A1 recall@3 {s['recall_at_3']}"  # type: ignore[operator]
    assert s["recall_at_1"] >= 0.55, f"A2 recall@1 {s['recall_at_1']}"  # type: ignore[operator]
    assert s["direct_recall_at_3"] >= 0.90, f"A3 direct recall@3 {s['direct_recall_at_3']}"  # type: ignore[operator]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN FAILURE, first run 2026-11-13: A2 recall@1 0.542 < 0.55 (13 of 24; one question "
        "short). A1 0.833 and A3 1.0 passed. Misses are paraphrases that share no words with the "
        "answer (a bag of words cannot match them) plus 'What does ACOS mean?', where the Target "
        "ACOS chunk outranked the definition. No score cut-off separates answerable (min top-1 "
        "0.094) from unanswerable (max 0.262) questions, so a minimum-score filter is not "
        "justified for this embedder."
    ),
)
async def test_retrieval_with_the_offline_hashing_embedder() -> None:
    _check(await _score("hashing", HashingEmbedder()))


@pytest.mark.skipif(
    not os.environ.get("RUN_LLM_EVAL"), reason="set RUN_LLM_EVAL=1 (uses the real embedding model)"
)
async def test_retrieval_with_the_configured_embedding_model() -> None:
    from app.config import Settings
    from app.infrastructure.llm.factory import build_ingest_embedder

    embedder = build_ingest_embedder(Settings())
    _check(await _score(embedder.model.replace("/", "_"), embedder))
