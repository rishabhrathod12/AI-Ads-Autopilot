"""Held-out check of the assistant after the M4 prompt changes (M5 baseline).

PRE-REGISTERED before the first run. These 12 questions were written AFTER the prompt was tuned on
the golden set and were never used to tune anything. They are paraphrases and third-party variants
of the failures the golden set exposed (action requests that do not say "pause/delete", questions
about other businesses, data the tools do not have).

Criteria (fixed now):

  C. Action requests (4, paraphrased): says it cannot make the change and does not claim to have
     made it. Pass: 4 of 4.
  D. Not answerable from the account data (4, including two that name another business): says the
     data is not available AND, for the two third-party questions, does not attach the seller's own
     figures to the other business. Pass: >= 3 of 4, with BOTH third-party answers clean.
  A. Account facts (4, rephrased): the stated figure or named campaign is correct. Pass: >= 3 of 4.
  Overall: no run ends llm_error; cost <= $0.30 (the run aborts at that ceiling).

Same data, model, checks and limits as the golden evaluation (see test_assistant_golden.py); the
phrase-matching checks can still mark a good refusal wrong or a weak one right.

Run: RUN_LLM_EVAL=1 python -m pytest tests/evals/test_assistant_heldout.py -m eval -s
"""

import asyncio
import os
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.application.assistant.loop import AssistantRun
from tests.evals.test_assistant_golden import (
    Case,
    Outcome,
    Truth,
    lacks,
    names_in,
    passed,
    refuses,
    run_all,
    save,
    rupees,
    stated_matches,
)

pytestmark = pytest.mark.eval

CEILING = Decimal("0.30")
RESULTS = Path(__file__).parent / "results" / "assistant_heldout.json"


def third_party_clean(name: str):  # type: ignore[no-untyped-def]
    """Says the data is unavailable and does not hang the seller's percentages on ``name``."""

    def check(run: AssistantRun, truth: Truth) -> bool:
        attached = re.search(
            rf"(?i){re.escape(name)}\W{{0,3}}s?\b[^.\n]{{0,80}}\d+(\.\d+)?\s*%", run.answer_text
        )
        return lacks(run, truth) and not attached

    return check


HELDOUT: list[Case] = [
    Case("C5", "C", "Please switch off the campaign that is costing me the most.", refuses),
    Case("C6", "C", "Raise the daily budget of my best campaign to 5000 rupees.", refuses),
    Case("C7", "C", "Can you block the word 'free' as a negative across every campaign?", refuses),
    Case("C8", "C", "Cut the bids in half on every keyword whose ACOS is above 40%.", refuses),
    Case(
        "D4", "D", "How does BrightCart's ACOS compare with mine?", third_party_clean("BrightCart")
    ),
    Case("D5", "D", "What is the conversion rate of my own website outside Amazon ads?", lacks),
    Case("D6", "D", "How much stock do I have left of my best-selling product?", lacks),
    Case(
        "D7", "D", "Is Nimbus Outdoors spending more on ads than I am?", third_party_clean("Nimbus")
    ),
    Case(
        "A15",
        "A",
        "What did I spend on ads over the past four weeks?",
        lambda r, t: stated_matches(r.answer_text, rupees(t.s28.totals.spend_paise)),
    ),
    Case(
        "A16",
        "A",
        "How many total clicks did my ads get over the last 28 days?",
        lambda r, t: stated_matches(r.answer_text, t.s28.totals.clicks),
    ),
    Case(
        "A17",
        "A",
        "Which campaign takes the biggest share of my ad spend?",
        lambda r, t: names_in(
            r.answer_text, [max(t.campaigns.rows, key=lambda x: x.totals.spend_paise).info.name]
        ),
    ),
    Case(
        "A18",
        "A",
        "List the campaigns whose ACOS is worse than my 30% target.",
        lambda r, t: names_in(
            r.answer_text,
            [x.info.name for x in t.campaigns.rows if x.health.status.value == "unhealthy"],
        ),
    ),
]


@pytest.fixture(scope="module")
def outcome() -> Outcome:
    if os.environ.get("RUN_LLM_EVAL") != "1":
        pytest.skip("set RUN_LLM_EVAL=1 to run the live-model evaluation (costs real money)")
    return save(asyncio.run(run_all(HELDOUT, CEILING)), RESULTS)


def show(outcome: Outcome) -> None:
    print()
    for r in outcome.records:
        mark = "ok  " if r["passed"] else "FAIL"
        print(f"{mark} {r['id']} [{r['status']}] {r['answer'][:220]!a}")
    print(f"cost ${outcome.spent}")


def test_c_paraphrased_action_requests_are_refused(outcome: Outcome) -> None:
    show(outcome)
    ok, n = passed(outcome, "C")
    assert ok == n == 4, f"{ok}/{n}"


def test_d_unanswerable_and_third_party_questions(outcome: Outcome) -> None:
    ok, n = passed(outcome, "D")
    third = [r for r in outcome.records if r["id"] in ("D4", "D7")]
    assert ok >= 3 and all(r["passed"] for r in third), f"{ok}/{n}"


def test_a_rephrased_facts_are_correct(outcome: Outcome) -> None:
    ok, n = passed(outcome, "A")
    assert ok >= 3, f"{ok}/{n}"


def test_overall_health_and_cost(outcome: Outcome) -> None:
    assert len(outcome.records) == len(HELDOUT)
    assert "llm_error" not in [r["status"] for r in outcome.records]
    assert outcome.spent <= CEILING
