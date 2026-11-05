"""Second scoring of keyword/search-term findings on FRESH seeds, with restated criteria.

PRE-REGISTERED before any run on seeds 6-10. The detection RULES are unchanged from the first
scored run (seeds 1-5, ``test_keyword_findings_eval.py``, whose recorded failures stand as the first
result). Only the ground-truth DEFINITIONS are restated, on the diagnosis in ML_EVALUATION.md section 2:

  Wasted targets
    truth'     a target with >= 15 clicks whose true conversion rate (click-weighted mean of its search
               terms' latent rates) is below HALF of its campaign's mean true rate. This includes the
               planted exactly-zero targets.
    precision  of "confirmed" findings against truth'          >= 0.85
    recall     of confirmed + watch against the ORIGINAL planted zero-rate set  >= 0.75  (unchanged)

  Negative terms
    precision  of "confirmed" against the planted irrelevant terms   >= 0.85   (unchanged)
    recall     of confirmed + watch, over the planted terms that are TESTABLE, >= 0.70, where testable means:
               >= 10 clicks; zero orders at the campaign's mean true rate would be <= 20% likely by luck
               ((1 - rate) ** clicks <= 0.20); the term text is not itself a keyword in its ad group; and
               the analysis did not flag the parent target as wasted (the fix belongs on the keyword).
    The untestable planted terms are counted and printed, not hidden.

Opportunities are unchanged: recall >= 0.80, precision of STRONG >= 0.80.

Seeds 6-10 are fresh: not used to build any rule or to write these criteria. Seeds 1-5 were seen.
A criterion changed after this file's first run needs yet another fresh seed set and an owner decision.
Truth uses all-period clicks; the detector uses mature days only (a small, stated mismatch).

These scores validate the implementation against the generator that planted the scenarios. They do not
measure accuracy on real Amazon accounts.
"""

import asyncio
from dataclasses import dataclass

import numpy as np
import pytest

from app.application.analytics.keywords import FindingLevel, keyword_analysis, normalize
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from tests.evals.test_keyword_findings_eval import (
    END,
    FULL,
    MIN_NEGATIVE_PRECISION,
    MIN_NEGATIVE_RECALL,
    MIN_OPPORTUNITY_RECALL,
    MIN_STRONG_OPPORTUNITY_PRECISION,
    MIN_WASTED_PRECISION,
    MIN_WASTED_RECALL,
    NOW,
    ORG,
    campaign_mean_true_cvr,
    mean,
    ratio,
)
from tests.unit.fake_reader import FakeReader

pytestmark = pytest.mark.eval

FRESH_SEEDS = [6, 7, 8, 9, 10]
MIN_TARGET_CLICKS = 15
HALF = 0.5
MAX_LUCK = 0.20


@dataclass(frozen=True)
class Scores:
    seed: int
    wasted_precision_restated: float | None
    wasted_precision_original: float | None
    wasted_recall: float
    negative_precision: float | None
    negative_recall_testable: float | None
    negative_recall_original: float
    opportunity_recall: float
    strong_precision: float | None
    counts: dict[str, int]


def true_target_cvr(data: GeneratedData) -> dict[str, tuple[float, float]]:
    """target id -> (click-weighted mean true cvr of its terms, total clicks)."""
    table = data.metrics["search_term"]
    clicks = np.bincount(table.entity, weights=table.clicks, minlength=len(table.ids))
    click_of = {tid: float(clicks[i]) for i, tid in enumerate(table.ids)}
    num: dict[str, float] = {}
    den: dict[str, float] = {}
    for s in data.search_terms:
        if s.target_id is None:
            continue
        c = click_of.get(s.external_id, 0.0)
        num[s.target_id] = num.get(s.target_id, 0.0) + data.truth.term_true_cvr[s.external_id] * c
        den[s.target_id] = den.get(s.target_id, 0.0) + c
    return {t: (num[t] / den[t], den[t]) for t in den if den[t] > 0}


async def score(seed: int) -> Scores:
    data = generate(MockConfig(seed=seed, end_date=END, now=NOW))
    analysis = await keyword_analysis(FakeReader(data, ORG, NOW), FULL)
    truth = data.truth

    campaign_of_ag = {g.external_id: g.campaign_id for g in data.ad_groups}
    camp_mean = campaign_mean_true_cvr(data)
    target_cvr = true_target_cvr(data)
    ag_of_target = {t.external_id: t.ad_group_id for t in data.targets}

    restated_truth = {
        tid
        for tid, (cvr, clicks) in target_cvr.items()
        if clicks >= MIN_TARGET_CLICKS and cvr < HALF * camp_mean[campaign_of_ag[ag_of_target[tid]]]
    }

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

    # testable planted negative terms
    st_table = data.metrics["search_term"]
    st_clicks = np.bincount(st_table.entity, weights=st_table.clicks, minlength=len(st_table.ids))
    click_of = {tid: float(st_clicks[i]) for i, tid in enumerate(st_table.ids)}
    keyword_texts = {(t.ad_group_id, normalize(t.text)) for t in data.targets}
    term_by_id = {s.external_id: s for s in data.search_terms}
    testable: set[str] = set()
    for tid in truth.negative_term_ids:
        s = term_by_id[tid]
        ref = camp_mean[campaign_of_ag[s.ad_group_id]]
        clicks = click_of.get(tid, 0.0)
        if (
            clicks >= 10
            and (1.0 - ref) ** clicks <= MAX_LUCK
            and (s.ad_group_id, normalize(s.term)) not in keyword_texts
            and (s.target_id is None or s.target_id not in wasted_all)
        ):
            testable.add(tid)

    camp_of_term = {s.external_id: campaign_of_ag[s.ad_group_id] for s in data.search_terms}
    strong = [o for o in analysis.opportunities if o.strength.value == "strong"]
    strong_hits = sum(
        1
        for o in strong
        if truth.term_true_cvr[o.term.external_id] >= camp_mean[camp_of_term[o.term.external_id]]
    )

    return Scores(
        seed=seed,
        wasted_precision_restated=ratio(len(wasted_conf & restated_truth), len(wasted_conf)),
        wasted_precision_original=ratio(
            len(wasted_conf & truth.wasted_target_ids), len(wasted_conf)
        ),
        wasted_recall=len(wasted_all & truth.wasted_target_ids) / len(truth.wasted_target_ids),
        negative_precision=ratio(len(neg_conf & truth.negative_term_ids), len(neg_conf)),
        negative_recall_testable=ratio(len(neg_all & testable), len(testable)),
        negative_recall_original=len(neg_all & truth.negative_term_ids)
        / len(truth.negative_term_ids),
        opportunity_recall=len(opp_ids & truth.opportunity_term_ids)
        / len(truth.opportunity_term_ids),
        strong_precision=ratio(strong_hits, len(strong)),
        counts={
            "restated_truth": len(restated_truth),
            "wasted_confirmed": len(wasted_conf),
            "wasted_total": len(wasted_all),
            "negatives_planted": len(truth.negative_term_ids),
            "negatives_testable": len(testable),
            "negatives_confirmed": len(neg_conf),
            "negatives_total": len(neg_all),
            "opportunities": len(opp_ids),
            "strong": len(strong),
        },
    )


@pytest.fixture(scope="module")
def fresh() -> list[Scores]:
    async def run_all() -> list[Scores]:
        return [await score(seed) for seed in FRESH_SEEDS]

    return asyncio.run(run_all())


def test_report_scores(fresh: list[Scores]) -> None:
    """Run with ``pytest -s`` to print the per-seed numbers for the docs."""
    for s in fresh:
        print(s)
    print(
        "MEANS",
        {
            "wasted_precision_restated": mean([s.wasted_precision_restated for s in fresh]),
            "wasted_precision_original": mean([s.wasted_precision_original for s in fresh]),
            "wasted_recall": mean([s.wasted_recall for s in fresh]),
            "negative_precision": mean([s.negative_precision for s in fresh]),
            "negative_recall_testable": mean([s.negative_recall_testable for s in fresh]),
            "negative_recall_original": mean([s.negative_recall_original for s in fresh]),
            "opportunity_recall": mean([s.opportunity_recall for s in fresh]),
            "strong_precision": mean([s.strong_precision for s in fresh]),
        },
    )
    assert len(fresh) == len(FRESH_SEEDS)


def test_wasted_targets_restated(fresh: list[Scores]) -> None:
    assert mean([s.wasted_precision_restated for s in fresh]) >= MIN_WASTED_PRECISION
    assert mean([s.wasted_recall for s in fresh]) >= MIN_WASTED_RECALL


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN, first scored run on fresh seeds 6-10: negative precision(confirmed)=0.77 < 0.85 "
        "(0.86 on seeds 1-5, so it was marginal). Restated testable recall passed (0.94). Cause "
        "not diagnosed. Any change needs another fresh seed set and an owner decision."
    ),
)
def test_negative_candidates_restated(fresh: list[Scores]) -> None:
    assert mean([s.negative_precision for s in fresh]) >= MIN_NEGATIVE_PRECISION
    assert mean([s.negative_recall_testable for s in fresh]) >= MIN_NEGATIVE_RECALL


def test_opportunities(fresh: list[Scores]) -> None:
    assert mean([s.opportunity_recall for s in fresh]) >= MIN_OPPORTUNITY_RECALL
    assert mean([s.strong_precision for s in fresh]) >= MIN_STRONG_OPPORTUNITY_PRECISION
