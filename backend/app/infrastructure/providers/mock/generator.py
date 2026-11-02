"""Deterministic synthetic Sponsored Products data. EVERYTHING produced here is labeled MOCK.

Bottom-up model: latent behaviour is drawn per search term; targets, ad groups and campaigns are
exact sums of their terms, so every level is internally consistent. A fixed seed reproduces the
dataset exactly. Synthetic data demonstrates the *method*; it says nothing about real accounts.

Planted scenarios (recorded in ``GroundTruth`` so later milestones can be evaluated):
  * wasted keywords      - real traffic, zero conversion
  * irrelevant terms     - negative-keyword candidates (auto/broad traffic that never converts)
  * winning auto terms   - converting search terms worth promoting to keywords
  * high-ACOS campaign   - bids too high, conversion too low
  * budget-capped winner - efficient campaign throttled by a small daily budget
  * anomaly campaign     - conversion rate halves during the last 14 days
  * attribution lag      - the last 3 days under-report orders and sales (``is_mature=False``)
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.domain.ads.models import (
    AdGroup,
    Campaign,
    DataSource,
    MatchType,
    Profile,
    SearchTerm,
    State,
    Target,
    TargetingType,
)

IntArr = NDArray[np.int64]
FloatArr = NDArray[np.float64]

PROFILE_ID = "MOCK-PRF-001"
ATTRIBUTION_DAYS = 7
LAG_FACTORS = {0: 0.55, 1: 0.8, 2: 0.92}  # days before the last day -> share of orders attributed
ANOMALY_DAYS = 14

# (role, targeting type); campaign i uses entry i % 6.
ROLE_CYCLE: list[tuple[str, TargetingType]] = [
    ("healthy", TargetingType.MANUAL),
    ("healthy", TargetingType.AUTO),
    ("high_acos", TargetingType.MANUAL),
    ("budget_capped", TargetingType.MANUAL),
    ("healthy", TargetingType.MANUAL),
    ("anomaly", TargetingType.MANUAL),
]

NICHES = [
    "wireless earbuds",
    "yoga mat",
    "steel water bottle",
    "led desk lamp",
    "running shoes",
    "protein powder",
    "phone case",
    "laptop stand",
    "kitchen knife set",
    "travel backpack",
]
MODIFIERS = [
    "best", "buy", "online", "cheap", "for women", "for men", "under 500", "premium", "combo",
    "pack of 2", "india", "set", "latest", "durable", "lightweight", "waterproof", "gift",
    "branded", "original", "with warranty",
]  # fmt: skip
VARIANTS = ["pro", "2 pack", "new", "offer", "black", "large", "small", "deal"]
IRRELEVANT = [
    "free", "diy", "repair", "wallpaper", "toy", "second hand", "manual pdf", "drawing", "olx",
    "wholesale supplier",
]  # fmt: skip
BRANDS = ["zenix", "aurora", "nova", "pulse", "orbit"]
AUTO_GROUPS = ["close-match", "loose-match", "substitutes", "complements"]
AUTO_WEIGHTS = [0.4, 0.3, 0.15, 0.15]
DOW_MULT = np.array([0.95, 0.97, 1.0, 1.0, 1.04, 1.08, 0.96])  # Mon..Sun


@dataclass(frozen=True)
class MockConfig:
    seed: int = 42
    days: int = 120
    end_date: date = field(default_factory=lambda: date.today() - timedelta(days=1))
    n_campaigns: int = 12
    ad_groups_per_campaign: int = 3
    keywords_per_ad_group: int = 55
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.days < 30:
            raise ValueError("days must be >= 30 (anomaly window + attribution lag)")
        if self.n_campaigns < len(ROLE_CYCLE):
            raise ValueError(f"n_campaigns must be >= {len(ROLE_CYCLE)} to include every scenario")
        if self.ad_groups_per_campaign < 1 or self.keywords_per_ad_group < 5:
            raise ValueError("need >= 1 ad group per campaign and >= 5 keywords per ad group")


@dataclass(frozen=True)
class GroundTruth:
    """What was planted, computed from the generated data (not from intentions)."""

    wasted_target_ids: frozenset[str]
    negative_term_ids: frozenset[str]
    opportunity_term_ids: frozenset[str]
    high_acos_campaign_ids: frozenset[str]
    budget_capped_campaign_ids: frozenset[str]
    anomaly_campaign_ids: frozenset[str]
    anomaly_start: date
    # Each search term's latent conversion rate (fraction), before promo/anomaly multipliers.
    # Lets evaluations ask "does this flagged term truly convert well?". Mock-only.
    term_true_cvr: dict[str, float]


@dataclass(frozen=True)
class MetricsTable:
    """Columnar sparse daily metrics (rows exist only for days with impressions)."""

    ids: list[str]
    entity: IntArr
    day: IntArr
    impressions: IntArr
    clicks: IntArr
    spend_paise: IntArr
    sales_paise: IntArr
    orders: IntArr
    units: IntArr


@dataclass(frozen=True)
class GeneratedData:
    profile: Profile
    campaigns: list[Campaign]
    ad_groups: list[AdGroup]
    targets: list[Target]
    search_terms: list[SearchTerm]
    metrics: dict[str, MetricsTable]  # keyed by EntityType value
    truth: GroundTruth
    dates: list[date]
    attribution_days: int
    mature_through: int  # last day index (inclusive) whose attribution window is complete


def _lag_vector(days: int) -> FloatArr:
    from_end = days - 1 - np.arange(days)
    out = np.ones(days)
    for k, factor in LAG_FACTORS.items():
        out[from_end == k] = factor
    return out


def generate(cfg: MockConfig) -> GeneratedData:
    rng = np.random.default_rng(cfg.seed)
    D = cfg.days
    start = cfg.end_date - timedelta(days=D - 1)
    dates = [start + timedelta(days=i) for i in range(D)]
    demand_day = DOW_MULT[np.array([d.weekday() for d in dates])] * (1 + 0.0008 * np.arange(D))
    promo_demand = np.ones(D)
    promo_cvr = np.ones(D)
    promo_at = D - 45
    promo_demand[promo_at : promo_at + 2] = 1.6
    promo_cvr[promo_at : promo_at + 2] = 1.25
    anomaly_idx = D - ANOMALY_DAYS
    lag = _lag_vector(D)
    now = cfg.now

    def prov() -> dict[str, Any]:
        return {"data_source": DataSource.MOCK, "ingested_at": now}

    # ---- structure + latent parameters -------------------------------------------------------
    camp_meta: list[dict[str, Any]] = []
    ag_models: list[AdGroup] = []
    tgt_models: list[Target] = []
    term_models: list[SearchTerm] = []
    t_ag: list[int] = []  # target -> ad group index
    t_wasted: list[bool] = []
    s_target: list[int] = []  # term -> target index
    s_camp: list[int] = []
    s_imps: list[float] = []
    s_ctr: list[float] = []
    s_cpc: list[float] = []
    s_cvr: list[float] = []
    s_aov: list[float] = []
    s_flag: list[int] = []  # 0 normal, 1 irrelevant, 2 winner

    for c in range(cfg.n_campaigns):
        role, ttype = ROLE_CYCLE[c % len(ROLE_CYCLE)]
        niche = NICHES[c % len(NICHES)]
        aov = float(rng.integers(60_000, 150_000))
        base_cvr = float(rng.uniform(0.06, 0.10))
        base_cpc = float(rng.integers(900, 1_800))
        camp_imps = float(rng.uniform(0.8, 1.4)) * 6_000
        cpc_mult = {"high_acos": 1.9, "budget_capped": 0.85}.get(role, 1.0)
        cvr_mult = {"high_acos": 0.65, "budget_capped": 1.35}.get(role, 1.0)
        camp_meta.append({"role": role, "type": ttype, "niche": niche})

        for g in range(cfg.ad_groups_per_campaign):
            a = len(ag_models)
            ag_imps = camp_imps / cfg.ad_groups_per_campaign
            specs: list[tuple[str, MatchType, float]] = []
            if ttype is TargetingType.MANUAL:
                n = cfg.keywords_per_ad_group
                w = 1.0 / np.arange(1, n + 1) ** 0.9
                rng.shuffle(w)
                w = w / w.sum()
                seen: set[tuple[str, MatchType]] = set()
                for i in range(n):
                    match = MatchType(
                        str(rng.choice(["broad", "phrase", "exact"], p=[0.4, 0.35, 0.25]))
                    )
                    text = niche
                    for _ in range(30):
                        k = int(rng.integers(0, 3))
                        mods = [
                            MODIFIERS[int(j)] for j in rng.choice(len(MODIFIERS), k, replace=False)
                        ]
                        text = " ".join([niche, *mods])
                        if (text, match) not in seen:
                            break
                    seen.add((text, match))
                    specs.append((text, match, float(w[i])))
            else:
                specs = [
                    (name, MatchType.AUTO, wt)
                    for name, wt in zip(AUTO_GROUPS, AUTO_WEIGHTS, strict=True)
                ]

            bids: list[int] = []
            for text, match, weight in specs:
                t = len(tgt_models)
                m_ctr = {MatchType.EXACT: 1.3, MatchType.BROAD: 0.8}.get(match, 1.0)
                m_cvr = {MatchType.EXACT: 1.15, MatchType.BROAD: 0.9}.get(match, 1.0)
                ctr_t = float(rng.beta(5, 995)) * m_ctr
                cpc_t = base_cpc * cpc_mult * float(rng.lognormal(0, 0.2))
                cvr_t = min(0.45, base_cvr * cvr_mult * float(rng.lognormal(0, 0.5)) * m_cvr)
                bid = round(cpc_t * float(rng.uniform(1.06, 1.5)))
                bids.append(bid)
                wasted = (
                    ttype is TargetingType.MANUAL
                    and weight >= 1.5 / len(specs)  # enough traffic to burn money
                    and float(rng.random()) < 0.12
                )
                t_wasted.append(wasted)
                if wasted:
                    cvr_t = 0.0
                tgt_models.append(
                    Target(
                        external_id=f"MOCK-TGT-{t:05d}",
                        ad_group_id=f"MOCK-AG-{a:04d}",
                        text=text,
                        match_type=match,
                        bid_paise=bid,
                        state=State.ENABLED,
                        **prov(),
                    )
                )
                t_ag.append(a)

                # search terms of this target
                if ttype is TargetingType.MANUAL:
                    extras = int(rng.integers(0, 4))
                    n_terms = 1 + extras
                    alpha = np.array([6.0] + [1.0] * extras)
                else:
                    n_terms = int(rng.integers(8, 15))
                    alpha = np.full(n_terms, 0.8)
                share = rng.dirichlet(alpha)
                used: set[str] = set()
                for j in range(n_terms):
                    flag = 0
                    if ttype is TargetingType.MANUAL and j == 0:
                        term_text = text
                    else:
                        r = float(rng.random())
                        if r < (0.12 if ttype is TargetingType.AUTO else 0.08):
                            flag = 1
                            term_text = f"{niche} {IRRELEVANT[int(rng.integers(len(IRRELEVANT)))]}"
                        elif ttype is TargetingType.AUTO and r > 0.92 and not wasted:
                            flag = 2
                            term_text = f"{BRANDS[int(rng.integers(len(BRANDS)))]} {niche}"
                        elif ttype is TargetingType.MANUAL:
                            term_text = f"{text} {VARIANTS[int(rng.integers(len(VARIANTS)))]}"
                        else:
                            term_text = f"{niche} {MODIFIERS[int(rng.integers(len(MODIFIERS)))]}"
                    while term_text in used:
                        term_text += " x"
                    used.add(term_text)
                    s = len(term_models)
                    term_models.append(
                        SearchTerm(
                            external_id=f"MOCK-ST-{s:05d}",
                            ad_group_id=f"MOCK-AG-{a:04d}",
                            target_id=f"MOCK-TGT-{t:05d}",
                            term=term_text,
                            **prov(),
                        )
                    )
                    s_target.append(t)
                    s_camp.append(c)
                    s_imps.append(ag_imps * weight * float(share[j]))
                    s_ctr.append(ctr_t * float(rng.lognormal(0, 0.25)))
                    s_cpc.append(min(cpc_t * float(rng.lognormal(0, 0.08)), bid * 0.94))
                    cvr_s = (
                        0.0
                        if (wasted or flag == 1)
                        else min(
                            0.45, cvr_t * float(rng.lognormal(0, 0.4)) * (2.5 if flag == 2 else 1)
                        )
                    )
                    s_cvr.append(cvr_s)
                    s_aov.append(aov)
                    s_flag.append(flag)

            ag_models.append(
                AdGroup(
                    external_id=f"MOCK-AG-{a:04d}",
                    campaign_id=f"MOCK-CMP-{c:03d}",
                    name=f"{niche} | group {g + 1}",
                    default_bid_paise=int(np.median(bids)),
                    state=State.ENABLED,
                    **prov(),
                )
            )

    C, S = cfg.n_campaigns, len(term_models)
    sc = np.array(s_camp)
    ctr = np.array(s_ctr)
    cpc = np.array(s_cpc)
    cvr = np.array(s_cvr)
    aov_arr = np.array(s_aov)

    # ---- daily simulation (terms x days) -----------------------------------------------------
    base = np.array(s_imps)[:, None] * (demand_day * promo_demand)[None, :]
    exp_spend = base * ctr[:, None] * cpc[:, None]
    camp_exp = np.zeros((C, D))
    np.add.at(camp_exp, sc, exp_spend)

    roles = [m["role"] for m in camp_meta]
    budget = np.array(
        [
            0.55 * camp_exp[c].mean() if roles[c] == "budget_capped" else 1.6 * camp_exp[c].max()
            for c in range(C)
        ]
    )
    budget = np.maximum(np.rint(budget / 100) * 100, 10_000)
    cap = np.minimum(1.0, budget[:, None] / np.maximum(camp_exp, 1.0))

    anomaly = np.ones((C, D))
    for c in range(C):
        if roles[c] == "anomaly":
            anomaly[c, anomaly_idx:] = 0.5

    imps: IntArr = rng.poisson(base * cap[sc]).astype(np.int64)
    clicks: IntArr = rng.binomial(imps, ctr[:, None]).astype(np.int64)
    cvr_mat = np.minimum(1.0, cvr[:, None] * promo_cvr[None, :] * anomaly[sc])
    orders_full = rng.binomial(clicks, cvr_mat)
    orders: IntArr = rng.binomial(orders_full, np.broadcast_to(lag, orders_full.shape)).astype(
        np.int64
    )
    units: IntArr = orders + rng.poisson(0.08 * orders).astype(np.int64)
    noise = np.clip(rng.normal(1.0, 0.05, imps.shape), 0.85, 1.04)
    spend: IntArr = np.rint(clicks * cpc[:, None] * noise).astype(np.int64)
    gamma = rng.gamma(np.maximum(orders, 1) * 6.0, aov_arr[:, None] / 6.0)
    sales: IntArr = np.where(orders > 0, np.rint(gamma), 0).astype(np.int64)
    term_m = {
        "impressions": imps, "clicks": clicks, "spend_paise": spend,
        "sales_paise": sales, "orders": orders, "units": units,
    }  # fmt: skip

    def roll(idx: IntArr, n: int) -> dict[str, IntArr]:
        out: dict[str, IntArr] = {}
        for k, v in term_m.items():
            a = np.zeros((n, D), dtype=np.int64)
            np.add.at(a, idx, v)
            out[k] = a
        return out

    st = np.array(s_target)
    tag = np.array(t_ag)
    ag_camp = np.array([int(a.campaign_id.split("-")[-1]) for a in ag_models])
    tgt_m = roll(st, len(tgt_models))
    ag_m = roll(tag[st], len(ag_models))  # term -> target -> ad group
    camp_m = roll(ag_camp[tag][st], C)  # term -> target -> ad group -> campaign

    def table(ids: list[str], m: dict[str, IntArr]) -> MetricsTable:
        e, d = np.nonzero(m["impressions"])
        return MetricsTable(
            ids=ids,
            entity=e.astype(np.int64),
            day=d.astype(np.int64),
            **{k: v[e, d] for k, v in m.items()},
        )

    metrics = {
        "campaign": table([f"MOCK-CMP-{c:03d}" for c in range(C)], camp_m),
        "ad_group": table([a.external_id for a in ag_models], ag_m),
        "target": table([t.external_id for t in tgt_models], tgt_m),
        "search_term": table([s.external_id for s in term_models], term_m),
    }

    # ---- entities that depend on simulated data ---------------------------------------------
    campaigns = [
        Campaign(
            external_id=f"MOCK-CMP-{c:03d}",
            profile_id=PROFILE_ID,
            name=f"SP | {camp_meta[c]['type'].value.title()} | {camp_meta[c]['niche']}",
            state=State.ENABLED,
            targeting_type=camp_meta[c]["type"],
            daily_budget_paise=int(budget[c]),
            bidding_strategy="dynamic bids - down only",
            **prov(),
        )
        for c in range(C)
    ]
    profile = Profile(
        external_id=PROFILE_ID,
        name="MOCK Seller (synthetic)",
        marketplace="Amazon.in",
        country="IN",
        region="FE",
        currency="INR",
        timezone="Asia/Kolkata",
        **prov(),
    )

    # ---- ground truth, computed from the data actually produced ------------------------------
    flags = np.array(s_flag)
    t_clicks = tgt_m["clicks"].sum(axis=1)
    s_clicks = clicks.sum(axis=1)
    s_orders = orders.sum(axis=1)
    capped_share = (cap < 1.0).mean(axis=1)
    truth = GroundTruth(
        wasted_target_ids=frozenset(
            tgt_models[t].external_id
            for t in range(len(tgt_models))
            if t_wasted[t] and t_clicks[t] >= 15
        ),
        negative_term_ids=frozenset(
            term_models[s].external_id for s in range(S) if flags[s] == 1 and s_clicks[s] >= 10
        ),
        opportunity_term_ids=frozenset(
            term_models[s].external_id for s in range(S) if flags[s] == 2 and s_orders[s] >= 3
        ),
        high_acos_campaign_ids=frozenset(
            campaigns[c].external_id for c in range(C) if roles[c] == "high_acos"
        ),
        budget_capped_campaign_ids=frozenset(
            campaigns[c].external_id
            for c in range(C)
            if roles[c] == "budget_capped" and capped_share[c] >= 0.4
        ),
        anomaly_campaign_ids=frozenset(
            campaigns[c].external_id for c in range(C) if roles[c] == "anomaly"
        ),
        anomaly_start=dates[anomaly_idx],
        term_true_cvr={term_models[s].external_id: float(cvr[s]) for s in range(S)},
    )
    return GeneratedData(
        profile=profile,
        campaigns=campaigns,
        ad_groups=ag_models,
        targets=tgt_models,
        search_terms=term_models,
        metrics=metrics,
        truth=truth,
        dates=dates,
        attribution_days=ATTRIBUTION_DAYS,
        mature_through=D - 1 - len(LAG_FACTORS),
    )
