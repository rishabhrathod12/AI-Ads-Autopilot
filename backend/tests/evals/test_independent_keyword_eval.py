"""Keyword findings scored against ECONOMIC truth, on fresh seeds 41-45.

PRE-REGISTERED before any run. The earlier keyword evaluations scored findings against the
generator's planted categories ("irrelevant", "zero-rate"). That asks "did you find what the
generator planted?", not "would acting on this finding cost the seller money?". This evaluation
defines truth from the latent economics instead, so a finding is correct when the term/target
really is unprofitable:

    true ACOS  =  observed CPC  /  (latent conversion rate x account average order value)
    unprofitable  <=>  true ACOS > break-even ACOS (assumed 30%; an explicit assumption, because the
                       mock data carries no product margins)

Observed CPC = spend / clicks of the entity; AOV = total sales / total orders of the account; the
latent rate is the generator's true per-term rate (click-weighted for a target). Entities with
fewer than 15 clicks are not scored. The rules under test are unchanged (``keyword_analysis`` as
shipped).

Criteria (mean over seeds 41-45):
  K1  precision of CONFIRMED negative-term candidates (share truly unprofitable at 30%)   >= 0.85
  K2  precision of CONFIRMED wasted targets (share truly unprofitable at 30%)             >= 0.85
Reported, NOT scored (sensitivity to the assumption): the same precisions at 25% and 40%.

Limitation, stated up front: only the labels are independent. The data still comes from the mock
generator (fresh seeds, same structure), so this does not remove generator dependence; the
independent forecast evaluation does that for forecasting only. A failed criterion is recorded as a
known failure; changing the break-even assumption afterwards needs fresh seeds and an owner
decision. Seeds 41-45 are used for nothing else.

Synthetic data: these results say nothing about real Amazon accounts.
"""

import asyncio
import statistics
from dataclasses import dataclass

import numpy as np
import pytest

from app.application.analytics.keywords import FindingLevel, keyword_analysis
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from tests.evals.test_keyword_findings_eval import END, FULL, NOW, ORG
from tests.unit.fake_reader import FakeReader

pytestmark = pytest.mark.eval

SEEDS = [41, 42, 43, 44, 45]
MIN_CLICKS = 15
BREAK_EVEN = 0.30
SENSITIVITY = (0.25, 0.40)
MIN_PRECISION = 0.85


@dataclass(frozen=True)
class Result:
    seed: int
    neg_precision: dict[float, float | None]
    wasted_precision: dict[float, float | None]
    n_neg: int
    n_wasted: int


def _acos_by_term_and_target(
    data: GeneratedData,
) -> tuple[dict[str, float], dict[str, float]]:
    """(true ACOS per scored search term, true ACOS per scored target)."""
    t = data.metrics["search_term"]
    n = len(t.ids)
    clicks = np.bincount(t.entity, weights=t.clicks, minlength=n)
    spend = np.bincount(t.entity, weights=t.spend_paise, minlength=n)
    aov = float(t.sales_paise.sum()) / float(t.orders.sum())
    idx = {tid: i for i, tid in enumerate(t.ids)}
    by_term: dict[str, float] = {}
    t_clicks: dict[str, float] = {}
    t_spend: dict[str, float] = {}
    t_rate_x_clicks: dict[str, float] = {}
    for s in data.search_terms:
        i = idx.get(s.external_id)
        if i is None or clicks[i] < 1:
            continue
        rate = data.truth.term_true_cvr[s.external_id]
        if clicks[i] >= MIN_CLICKS and rate > 0:
            by_term[s.external_id] = (spend[i] / clicks[i]) / (rate * aov)
        elif clicks[i] >= MIN_CLICKS:
            by_term[s.external_id] = float("inf")
        if s.target_id is not None:
            t_clicks[s.target_id] = t_clicks.get(s.target_id, 0.0) + clicks[i]
            t_spend[s.target_id] = t_spend.get(s.target_id, 0.0) + spend[i]
            t_rate_x_clicks[s.target_id] = t_rate_x_clicks.get(s.target_id, 0.0) + rate * clicks[i]
    by_target: dict[str, float] = {}
    for tid, c in t_clicks.items():
        if c >= MIN_CLICKS:
            rate = t_rate_x_clicks[tid] / c
            by_target[tid] = (t_spend[tid] / c) / (rate * aov) if rate > 0 else float("inf")
    return by_term, by_target


def _precision(flagged: set[str], acos: dict[str, float], b: float) -> float | None:
    scored = [x for x in flagged if x in acos]
    return sum(acos[x] > b for x in scored) / len(scored) if scored else None


async def score(seed: int) -> Result:
    data = generate(MockConfig(seed=seed, end_date=END, now=NOW))
    analysis = await keyword_analysis(FakeReader(data, ORG, NOW), FULL)
    by_term, by_target = _acos_by_term_and_target(data)
    neg = {
        n.term.external_id
        for n in analysis.negative_candidates
        if n.level is FindingLevel.CONFIRMED
    }
    wasted = {
        w.target.external_id for w in analysis.wasted_targets if w.level is FindingLevel.CONFIRMED
    }
    levels = (BREAK_EVEN, *SENSITIVITY)
    return Result(
        seed=seed,
        neg_precision={b: _precision(neg, by_term, b) for b in levels},
        wasted_precision={b: _precision(wasted, by_target, b) for b in levels},
        n_neg=len(neg),
        n_wasted=len(wasted),
    )


def _mean(values: list[float | None]) -> float:
    return statistics.fmean(v for v in values if v is not None)


@pytest.fixture(scope="module")
def results() -> list[Result]:
    async def run_all() -> list[Result]:
        return [await score(s) for s in SEEDS]

    return asyncio.run(run_all())


def test_report(results: list[Result]) -> None:
    """Run with ``pytest -s -m eval`` to print the numbers for the docs."""
    for r in results:
        print(r)
    for b in (BREAK_EVEN, *SENSITIVITY):
        print(
            f"break-even {b:.2f}: negative precision "
            f"{_mean([r.neg_precision[b] for r in results]):.3f}"
            f", wasted precision {_mean([r.wasted_precision[b] for r in results]):.3f}"
        )


def test_confirmed_negatives_are_economically_unprofitable(results: list[Result]) -> None:
    assert _mean([r.neg_precision[BREAK_EVEN] for r in results]) >= MIN_PRECISION  # K1


def test_confirmed_wasted_targets_are_economically_unprofitable(results: list[Result]) -> None:
    assert _mean([r.wasted_precision[BREAK_EVEN] for r in results]) >= MIN_PRECISION  # K2
