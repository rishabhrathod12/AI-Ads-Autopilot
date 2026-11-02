"""Scores keyword/search-term findings against the generator's planted ground truth.

PRE-REGISTERED before the first run on the held-out seeds. These numbers were fixed in advance
from the rules' design, not tuned to results:

  wasted targets   precision(confirmed) >= 0.85    recall(confirmed + watch) >= 0.75
  negative terms   precision(confirmed) >= 0.85    recall(confirmed + watch) >= 0.70
                   (recall is lower by design: terms that are themselves keywords, or whose parent
                    keyword is already flagged, are excluded - the fix belongs on the keyword)
  opportunities    planted-winner recall >= 0.80
                   precision of STRONG items (true CVR >= their campaign's mean true CVR) >= 0.80

Seeds 1-5 are held out: no rule or threshold may be adjusted after looking at them. Seed 42 (the
default dataset) was inspected while building the rules, so it is never scored here.

These scores validate the implementation against the generator that created the scenarios. They
do not measure accuracy on real Amazon accounts.
"""

import asyncio
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import numpy as np
import pytest

from app.application.analytics.keywords import FindingLevel, keyword_analysis
from app.domain.kpi.periods import DateRange
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from tests.unit.fake_reader import FakeReader

pytestmark = pytest.mark.eval  # slow; excluded by default, run with: pytest -m eval

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, tzinfo=UTC)
ORG = UUID(int=9)
FULL = DateRange(END - timedelta(days=119), END)
HELD_OUT_SEEDS = [1, 2, 3, 4, 5]

MIN_WASTED_PRECISION = 0.85
MIN_WASTED_RECALL = 0.75
MIN_NEGATIVE_PRECISION = 0.85
MIN_NEGATIVE_RECALL = 0.70
MIN_OPPORTUNITY_RECALL = 0.80
MIN_STRONG_OPPORTUNITY_PRECISION = 0.80


@dataclass(frozen=True)
class Scores:
    seed: int
    wasted_precision: float | None
    wasted_recall: float
    negative_precision: float | None
    negative_recall: float
    opportunity_recall: float
    strong_precision: float | None
    counts: dict[str, int]


def ratio(hit: int, total: int) -> float | None:
    return hit / total if total else None


def campaign_mean_true_cvr(data: GeneratedData) -> dict[str, float]:
    """Click-weighted mean latent conversion rate per campaign, over all of its search terms."""
    table = data.metrics["search_term"]
    clicks = np.bincount(table.entity, weights=table.clicks, minlength=len(table.ids))
    click_of = {tid: float(clicks[i]) for i, tid in enumerate(table.ids)}
    campaign_of_ad_group = {g.external_id: g.campaign_id for g in data.ad_groups}
    weighted: dict[str, float] = {}
    total: dict[str, float] = {}
    for s in data.search_terms:
        camp = campaign_of_ad_group[s.ad_group_id]
        c = click_of.get(s.external_id, 0.0)
        weighted[camp] = weighted.get(camp, 0.0) + data.truth.term_true_cvr[s.external_id] * c
        total[camp] = total.get(camp, 0.0) + c
    return {camp: (weighted[camp] / total[camp] if total[camp] else 0.0) for camp in total}


async def score(seed: int) -> Scores:
    data = generate(MockConfig(seed=seed, end_date=END, now=NOW))
    analysis = await keyword_analysis(FakeReader(data, ORG, NOW), FULL)
    truth = data.truth

    wasted_all = {w.target.external_id for w in analysis.wasted_targets}
    wasted_conf = {
        w.target.external_id for w in analysis.wasted_targets if w.level is FindingLevel.CONFIRMED
    }
    neg_all = {n.term.external_id for n in analysis.negative_candidates}
    neg_conf = {
        n.term.external_id
        for n in analysis.negative_candidates
        if n.level is FindingLevel.CONFIRMED
    }
    opp_ids = {o.term.external_id for o in analysis.opportunities}

    camp_mean = campaign_mean_true_cvr(data)
    campaign_of_ad_group = {g.external_id: g.campaign_id for g in data.ad_groups}
    campaign_of_term = {
        s.external_id: campaign_of_ad_group[s.ad_group_id] for s in data.search_terms
    }
    strong = [o for o in analysis.opportunities if o.strength.value == "strong"]
    strong_hits = sum(
        1
        for o in strong
        if truth.term_true_cvr[o.term.external_id]
        >= camp_mean[campaign_of_term[o.term.external_id]]
    )

    return Scores(
        seed=seed,
        wasted_precision=ratio(len(wasted_conf & truth.wasted_target_ids), len(wasted_conf)),
        wasted_recall=len(wasted_all & truth.wasted_target_ids) / len(truth.wasted_target_ids),
        negative_precision=ratio(len(neg_conf & truth.negative_term_ids), len(neg_conf)),
        negative_recall=len(neg_all & truth.negative_term_ids) / len(truth.negative_term_ids),
        opportunity_recall=len(opp_ids & truth.opportunity_term_ids)
        / len(truth.opportunity_term_ids),
        strong_precision=ratio(strong_hits, len(strong)),
        counts={
            "wasted_confirmed": len(wasted_conf),
            "wasted_total": len(wasted_all),
            "negatives_confirmed": len(neg_conf),
            "negatives_total": len(neg_all),
            "opportunities": len(opp_ids),
            "strong": len(strong),
        },
    )


def mean(values: list[float | None]) -> float:
    return statistics.fmean(v for v in values if v is not None)


@pytest.fixture(scope="module")
def held_out() -> list[Scores]:
    async def run_all() -> list[Scores]:
        return [await score(seed) for seed in HELD_OUT_SEEDS]

    return asyncio.run(run_all())


def test_report_scores(held_out: list[Scores]) -> None:
    """Run with ``pytest -s`` to print the per-seed numbers for the docs."""
    for s in held_out:
        print(s)
    assert len(held_out) == len(HELD_OUT_SEEDS)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN, first scored run on seeds 1-5: precision(confirmed)=0.67 < 0.85. The "
        "pre-registered "
        "truth counts only exactly-zero converters, but every false positive converts below its "
        "campaign mean (70% below half). Criterion likely too narrow; needs an owner decision and "
        "fresh seeds before being changed."
    ),
)
def test_wasted_targets_meet_preregistered_thresholds(held_out: list[Scores]) -> None:
    assert mean([s.wasted_precision for s in held_out]) >= MIN_WASTED_PRECISION
    assert mean([s.wasted_recall for s in held_out]) >= MIN_WASTED_RECALL


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN, first scored run on seeds 1-5: recall=0.64 < 0.70. Of the missed planted terms, "
        "most have too few clicks to tell zero orders from bad luck, and ~1/5 are excluded by "
        "design because the parent keyword is already flagged. Needs an owner decision."
    ),
)
def test_negative_candidates_meet_preregistered_thresholds(held_out: list[Scores]) -> None:
    assert mean([s.negative_precision for s in held_out]) >= MIN_NEGATIVE_PRECISION
    assert mean([s.negative_recall for s in held_out]) >= MIN_NEGATIVE_RECALL


def test_opportunities_meet_preregistered_thresholds(held_out: list[Scores]) -> None:
    assert mean([s.opportunity_recall for s in held_out]) >= MIN_OPPORTUNITY_RECALL
    assert mean([s.strong_precision for s in held_out]) >= MIN_STRONG_OPPORTUNITY_PRECISION
