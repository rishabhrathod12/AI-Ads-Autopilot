from datetime import UTC, date, datetime

import numpy as np
import pytest

from app.domain.ads.models import DataSource
from app.infrastructure.providers.mock.generator import (
    GeneratedData,
    MetricsTable,
    MockConfig,
    generate,
)

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)


def cfg(**kw: object) -> MockConfig:
    return MockConfig(end_date=END, now=NOW, **kw)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def data() -> GeneratedData:
    return generate(cfg())


def totals(table: MetricsTable, name: str) -> dict[str, int]:
    sums = np.bincount(table.entity, weights=getattr(table, name), minlength=len(table.ids))
    return {eid: int(sums[i]) for i, eid in enumerate(table.ids)}


def sum_all(table: MetricsTable, name: str) -> int:
    return int(getattr(table, name).sum())


def test_default_is_120_days_and_realistic_size(data: GeneratedData) -> None:
    assert len(data.dates) == 120
    assert data.dates[-1] == END
    assert len(data.campaigns) == 12
    assert len(data.targets) > 1_000
    assert len(data.search_terms) > len(data.targets)


def test_same_seed_is_identical_and_different_seed_differs() -> None:
    a, b = generate(cfg(seed=7, days=40)), generate(cfg(seed=7, days=40))
    c = generate(cfg(seed=8, days=40))
    assert a.campaigns == b.campaigns
    assert a.search_terms == b.search_terms
    for key in a.metrics:
        assert np.array_equal(a.metrics[key].clicks, b.metrics[key].clicks)
        assert np.array_equal(a.metrics[key].sales_paise, b.metrics[key].sales_paise)
    assert a.truth == b.truth
    assert not np.array_equal(a.metrics["campaign"].clicks, c.metrics["campaign"].clicks)


@pytest.mark.parametrize("kwargs", [{"days": 10}, {"n_campaigns": 3}, {"keywords_per_ad_group": 2}])
def test_invalid_config_is_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        cfg(**kwargs)


def test_every_record_is_labeled_mock_with_timestamp(data: GeneratedData) -> None:
    records = [
        data.profile,
        *data.campaigns,
        *data.ad_groups,
        *data.targets,
        *data.search_terms,
    ]
    assert records
    for r in records:
        assert r.data_source is DataSource.MOCK
        assert r.ingested_at == NOW
    assert "MOCK" in data.profile.name


def test_hierarchy_is_consistent(data: GeneratedData) -> None:
    camps = {c.external_id for c in data.campaigns}
    groups = {g.external_id: g for g in data.ad_groups}
    targets = {t.external_id: t for t in data.targets}
    assert all(g.campaign_id in camps for g in groups.values())
    assert all(t.ad_group_id in groups for t in targets.values())
    for s in data.search_terms:
        assert s.target_id in targets
        assert targets[s.target_id].ad_group_id == s.ad_group_id
    assert len({t.external_id for t in data.targets}) == len(data.targets)
    assert len({s.external_id for s in data.search_terms}) == len(data.search_terms)


@pytest.mark.parametrize(
    "metric", ["impressions", "clicks", "spend_paise", "sales_paise", "orders", "units"]
)
def test_every_level_sums_to_the_same_totals(data: GeneratedData, metric: str) -> None:
    totals_by_level = {k: sum_all(t, metric) for k, t in data.metrics.items()}
    assert len(set(totals_by_level.values())) == 1, totals_by_level


def test_campaign_equals_sum_of_its_ad_groups(data: GeneratedData) -> None:
    by_ag = totals(data.metrics["ad_group"], "spend_paise")
    by_camp = totals(data.metrics["campaign"], "spend_paise")
    for camp_id, spend in by_camp.items():
        ag_sum = sum(by_ag[g.external_id] for g in data.ad_groups if g.campaign_id == camp_id)
        assert ag_sum == spend


def test_metric_invariants_hold_for_all_rows(data: GeneratedData) -> None:
    for t in data.metrics.values():
        assert (t.clicks <= t.impressions).all()
        assert (t.orders <= t.clicks).all()
        assert (t.units >= t.orders).all()
        assert (t.spend_paise[t.clicks == 0] == 0).all()
        assert (t.sales_paise[t.orders == 0] == 0).all()
        assert (t.impressions > 0).all()  # sparse: rows only for active days


def test_overall_numbers_are_plausible(data: GeneratedData) -> None:
    t = data.metrics["campaign"]
    imps, clicks, orders = (sum_all(t, n) for n in ("impressions", "clicks", "orders"))
    spend, sales = sum_all(t, "spend_paise"), sum_all(t, "sales_paise")
    assert 0.002 < clicks / imps < 0.012  # CTR 0.2% - 1.2%
    assert 0.03 < orders / clicks < 0.2  # CVR
    assert 0.08 < spend / sales < 0.45  # ACOS
    assert 500 < spend / clicks < 3_000  # CPC in paise (Rs 5 - Rs 30)


def test_traffic_has_a_long_tail(data: GeneratedData) -> None:
    clicks = np.sort(np.array(list(totals(data.metrics["target"], "clicks").values())))[::-1]
    top_decile = clicks[: len(clicks) // 10].sum()
    assert top_decile / clicks.sum() > 0.4
    assert (clicks < 20).mean() > 0.3  # many keywords have too little data to act on


def test_bids_cover_actual_cpc(data: GeneratedData) -> None:
    spend = totals(data.metrics["target"], "spend_paise")
    clicks = totals(data.metrics["target"], "clicks")
    checked = 0
    for t in data.targets:
        if clicks[t.external_id] >= 10:
            assert spend[t.external_id] / clicks[t.external_id] <= t.bid_paise
            checked += 1
    assert checked > 100


# ---- planted scenarios ---------------------------------------------------------------------
def test_wasted_keywords_have_traffic_but_no_orders(data: GeneratedData) -> None:
    truth = data.truth.wasted_target_ids
    assert len(truth) >= 5
    clicks, orders = (
        totals(data.metrics["target"], "clicks"),
        totals(data.metrics["target"], "orders"),
    )
    for tid in truth:
        assert clicks[tid] >= 15
        assert orders[tid] == 0


def test_negative_candidates_never_convert(data: GeneratedData) -> None:
    truth = data.truth.negative_term_ids
    assert len(truth) >= 5
    clicks = totals(data.metrics["search_term"], "clicks")
    orders = totals(data.metrics["search_term"], "orders")
    for sid in truth:
        assert clicks[sid] >= 10
        assert orders[sid] == 0


def test_opportunity_terms_convert(data: GeneratedData) -> None:
    truth = data.truth.opportunity_term_ids
    assert len(truth) >= 3
    orders = totals(data.metrics["search_term"], "orders")
    assert all(orders[s] >= 3 for s in truth)


def _acos(data: GeneratedData, camp_id: str) -> float:
    spend = totals(data.metrics["campaign"], "spend_paise")[camp_id]
    sales = totals(data.metrics["campaign"], "sales_paise")[camp_id]
    return spend / sales


def test_high_acos_campaigns_are_clearly_worse(data: GeneratedData) -> None:
    truth = data.truth
    others = [
        _acos(data, c.external_id)
        for c in data.campaigns
        if c.external_id not in truth.high_acos_campaign_ids
    ]
    assert len(truth.high_acos_campaign_ids) == 2
    for cid in truth.high_acos_campaign_ids:
        assert _acos(data, cid) > 1.5 * float(np.median(others))


def test_budget_capped_campaigns_hit_their_budget(data: GeneratedData) -> None:
    assert data.truth.budget_capped_campaign_ids
    t = data.metrics["campaign"]
    by_id = {c.external_id: c for c in data.campaigns}
    for cid in data.truth.budget_capped_campaign_ids:
        idx = t.ids.index(cid)
        daily = t.spend_paise[t.entity == idx]
        assert 0.8 < daily.mean() / by_id[cid].daily_budget_paise < 1.2
        # efficient but throttled: ACOS well below the account median
        assert _acos(data, cid) < 0.15


def test_anomaly_campaign_conversion_drops_in_last_14_days(data: GeneratedData) -> None:
    assert data.truth.anomaly_start == date(2026, 10, 18)
    t = data.metrics["campaign"]
    first_anomaly_day = (data.truth.anomaly_start - data.dates[0]).days
    for cid in data.truth.anomaly_campaign_ids:
        idx = t.ids.index(cid)
        mine = t.entity == idx
        recent = mine & (t.day >= first_anomaly_day) & (t.day <= data.mature_through)
        before = mine & (t.day < first_anomaly_day)
        cvr_recent = t.orders[recent].sum() / t.clicks[recent].sum()
        cvr_before = t.orders[before].sum() / t.clicks[before].sum()
        assert cvr_recent < 0.7 * cvr_before


def test_recent_days_are_under_reported_by_attribution_lag(data: GeneratedData) -> None:
    t = data.metrics["campaign"]
    ratio = {
        d: t.orders[t.day == d].sum() / t.clicks[t.day == d].sum()
        for d in (len(data.dates) - 1, len(data.dates) - 20, len(data.dates) - 21)
    }
    last = ratio[len(data.dates) - 1]
    mature = (ratio[len(data.dates) - 20] + ratio[len(data.dates) - 21]) / 2
    assert last < 0.8 * mature
    assert data.mature_through == len(data.dates) - 4
