"""DIAGNOSTIC, not a pass/fail evaluation: why did forecast question R3 end unverified?

Runs forecast-style questions through BOTH engines and prints the validator's violations
(stored by the harness since this change). Asserts nothing. Results are not used to tune
anything that an evaluation scores.

Run: RUN_LLM_EVAL=1 python -m pytest tests/evals/diag_forecast.py -m eval -s
"""

import asyncio
import os
from decimal import Decimal

import pytest

from tests.evals.test_assistant_golden import Case, run_all

pytestmark = pytest.mark.eval

QUESTIONS = [
    "What do you expect my spend and sales to do over the next two weeks?",
    "What is my ad sales forecast for the next 14 days?",
    "Will my ACOS go up or down in the next two weeks?",
]


def answered(run, _truth) -> bool:  # type: ignore[no-untyped-def]
    return run.status.value in ("answered", "partial")


def test_forecast_diagnostic() -> None:
    if os.environ.get("RUN_LLM_EVAL") != "1":
        pytest.skip("set RUN_LLM_EVAL=1 (costs real money)")
    cases = [Case(f"F{i}", "F", q, answered) for i, q in enumerate(QUESTIONS, 1)]
    for engine in ("single", "graph"):
        out = asyncio.run(run_all(cases, Decimal("0.10"), engine=engine))
        print(f"\n== {engine}: ${out.spent}")
        for r in out.records:
            print(f"{r['id']} [{r['status']}] first_draft_valid={r['first_draft_valid']}")
            for v in r["violations"]:
                print("   violation:", v[:300])
            print("   answer:", r["answer"][:300].replace("\n", " | "))
