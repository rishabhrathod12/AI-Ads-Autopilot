"""The assistant's tools: read-only, tenant-bound wrappers over the tested M3 analytics services.

There is deliberately no write tool and no parameter that names a tenant: the reader and forecast
store arrive already bound to one organization. Every figure the assistant states must trace to the
``Evidence`` a tool returned. Invalid input comes back as a ``ToolError`` the model can correct;
it never raises.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.application.analytics.anomalies import detect_anomalies
from app.application.analytics.campaigns import campaign_changes, campaign_overview
from app.application.analytics.dashboard import NoDataError, dashboard_summary
from app.application.analytics.keyword_tables import SortKey, target_table
from app.application.analytics.keywords import keyword_analysis
from app.application.assistant.evidence import (
    Evidence,
    ToolError,
    add_rupees,
    fit,
    scrub,
)
from app.application.forecasting.service import InsufficientHistoryError, NoTrafficError
from app.application.jsonable import to_jsonable
from app.application.ports.embedding import EmbeddingClient
from app.application.ports.forecast_store import ForecastStore
from app.application.ports.knowledge_store import KnowledgeStore
from app.application.ports.llm import LLMError, ToolCall, ToolSpec
from app.application.ports.performance_reader import Freshness, PerformanceReader, Provenance
from app.domain.kpi.metrics import acos_pct
from app.domain.kpi.periods import DateRange, NoMatureDataError


class KnowledgeUnavailableError(RuntimeError):
    pass


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


DAYS = Field(28, ge=1, le=366, description="Window length in days, ending on the latest data day.")
TARGET = Field(30.0, gt=0, le=1000, description="Target ACOS in percent used to judge health.")


class SummaryArgs(_Args):
    days: int = DAYS


class CampaignArgs(_Args):
    days: int = DAYS
    target_acos_pct: float = TARGET


class ChangesArgs(_Args):
    days: int = DAYS


class FindingsArgs(_Args):
    days: int = DAYS
    target_acos_pct: float = TARGET
    limit: int = Field(10, ge=1, le=25, description="Max items per list.")


class KeywordArgs(_Args):
    days: int = DAYS
    sort: Literal["spend", "sales", "clicks", "orders", "acos"] = "spend"
    limit: int = Field(10, ge=1, le=25)


class NoArgs(_Args):
    pass


class KnowledgeArgs(_Args):
    query: str = Field(
        min_length=3,
        max_length=300,
        description="What to look up, in plain words, e.g. 'what is ACOS' or 'when to add a "
        "negative keyword'.",
    )
    limit: int = Field(4, ge=1, le=6, description="How many passages to return.")


@dataclass(frozen=True)
class ToolContext:
    reader: PerformanceReader
    forecasts: ForecastStore
    knowledge: KnowledgeStore | None = None
    embedder: EmbeddingClient | None = None

    @property
    def has_knowledge(self) -> bool:
        return self.knowledge is not None and self.embedder is not None


@dataclass(frozen=True)
class Output:
    result: Any
    provenance: Provenance | None  # None for reference material: it is not account data
    freshness: Freshness | None
    period: DateRange | None = None
    drop: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    args: type[_Args]
    run: Callable[[ToolContext, Any], Awaitable[Output]]

    def spec(self) -> ToolSpec:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        schema["additionalProperties"] = False
        return ToolSpec(self.name, self.description, schema)


async def _window(ctx: ToolContext, days: int) -> DateRange:
    fresh = await ctx.reader.freshness()
    if fresh.latest_date is None:
        raise NoDataError("no advertising data")
    return DateRange(fresh.latest_date - timedelta(days=days - 1), fresh.latest_date)


async def _summary(ctx: ToolContext, a: SummaryArgs) -> Output:
    period = await _window(ctx, a.days)
    r = await dashboard_summary(ctx.reader, period)
    return Output(r, r.provenance, r.freshness, period, drop=("trend",))


async def _campaigns(ctx: ToolContext, a: CampaignArgs) -> Output:
    period = await _window(ctx, a.days)
    r = await campaign_overview(ctx.reader, period, target_acos_pct=Decimal(str(a.target_acos_pct)))
    return Output(r, r.provenance, r.freshness, r.period)


async def _changes(ctx: ToolContext, a: ChangesArgs) -> Output:
    period = await _window(ctx, a.days)
    changes = await campaign_changes(ctx.reader, period)
    fresh = await ctx.reader.freshness()
    return Output({"campaigns": changes}, await ctx.reader.provenance(period), fresh, period)


async def _findings(ctx: ToolContext, a: FindingsArgs) -> Output:
    period = await _window(ctx, a.days)
    full = await keyword_analysis(
        ctx.reader, period, target_acos_pct=Decimal(str(a.target_acos_pct))
    )
    trimmed = replace(
        full,
        wasted_targets=full.wasted_targets[: a.limit],
        underperforming_targets=full.underperforming_targets[: a.limit],
        negative_candidates=full.negative_candidates[: a.limit],
        opportunities=full.opportunities[: a.limit],
    )
    counts = {
        "wasted_targets": len(full.wasted_targets),
        "underperforming_targets": len(full.underperforming_targets),
        "negative_candidates": len(full.negative_candidates),
        "opportunities": len(full.opportunities),
        "limit_per_list": a.limit,
    }
    return Output(
        {"findings": trimmed, "counts_before_limit": counts},
        full.provenance,
        full.freshness,
        full.period,
    )


async def _keywords(ctx: ToolContext, a: KeywordArgs) -> Output:
    period = await _window(ctx, a.days)
    sort: SortKey = a.sort
    r = await target_table(ctx.reader, period, sort=sort, limit=a.limit)
    return Output(r, r.provenance, r.freshness, r.period)


async def _anomalies(ctx: ToolContext, _: NoArgs) -> Output:
    r = await detect_anomalies(ctx.reader)
    return Output(r, r.provenance, r.freshness, r.monitor)


async def _forecast(ctx: ToolContext, _: NoArgs) -> Output:
    """Reads the *stored* forecast only; computing one is slow and writes a row."""
    fresh = await ctx.reader.freshness()
    if fresh.latest_date is None:
        raise NoDataError("no advertising data")
    prov = await ctx.reader.provenance(await _window(ctx, 28))
    spend = await ctx.forecasts.latest("account", None, "spend")
    sales = await ctx.forecasts.latest("account", None, "sales")
    if spend is None or sales is None:
        data: dict[str, Any] = {
            "available": False,
            "message": "No forecast has been generated for this account yet.",
        }
        return Output(data, prov, fresh)
    total_spend = round(sum(p["point"] for p in spend.points))
    total_sales = round(sum(p["point"] for p in sales.points))
    data = {
        "available": True,
        "spend": spend,
        "sales": sales,
        "derived_acos_pct": acos_pct(total_spend, total_sales),
        "note": "ACOS is derived from the spend and sales forecasts over the horizon.",
    }
    return Output(data, Provenance(frozenset({spend.data_source}), spend.input_as_of), fresh)


async def _knowledge(ctx: ToolContext, a: KnowledgeArgs) -> Output:
    """Reference material (definitions, how-to guidance). It explains; it never reports the
    seller's numbers, and what it says about Amazon policy is the author's, not Amazon's."""
    if ctx.knowledge is None or ctx.embedder is None:
        raise KnowledgeUnavailableError
    response = await ctx.embedder.embed([a.query])
    passages = await ctx.knowledge.search(response.vectors[0], ctx.embedder.model, a.limit)
    result = {
        "query": a.query,
        "passages": [
            {
                "title": p.title,
                "source": p.source_key,
                "section": p.heading,
                "scope": p.scope,
                "passage": p.content,
            }
            for p in passages
        ],
        "note": "Reference material, not the seller's data. Ranked best match first; if nothing "
        "answers the question, say so rather than stretching a loosely related passage.",
    }
    return Output(result, None, None)


KNOWLEDGE_TOOL = ToolDef(
    "search_knowledge",
    "Look up PPC definitions and optimisation guidance (what a metric means, how ACOS differs "
    "from ROAS, when a negative keyword makes sense). Returns cited passages from reference "
    "documents. Use it for 'what is / how does / what should' questions; it never contains the "
    "seller's own figures.",
    KnowledgeArgs,
    _knowledge,
)

TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        "get_dashboard_summary",
        "Account totals and KPIs (spend, ad sales, ACOS, ROAS, CTR, CPC, conversion rate) for the "
        "last N days, compared with the equal-length period before it (mature days only), "
        "including which factor (CPC, conversion rate, order value) moved ACOS. Use for 'how am "
        "I doing' and 'why did my ACOS change'.",
        SummaryArgs,
        _summary,
    ),
    ToolDef(
        "get_campaign_overview",
        "Per-campaign KPIs, health status against a target ACOS, budget use, spend share. "
        "Use for 'which campaigns need attention / deserve more budget'.",
        CampaignArgs,
        _campaigns,
    ),
    ToolDef(
        "get_campaign_changes",
        "Per-campaign change in KPIs versus the previous period, with the ACOS driver breakdown. "
        "Use to see which campaign caused a change.",
        ChangesArgs,
        _changes,
    ),
    ToolDef(
        "get_keyword_findings",
        "Keywords wasting spend (zero orders after enough clicks), underperforming keywords, "
        "negative-keyword candidates and keyword opportunities, each with its evidence strength. "
        "Findings are observations, not actions.",
        FindingsArgs,
        _findings,
    ),
    ToolDef(
        "get_top_keywords",
        "Table of the top keywords/targets by spend, sales, clicks, orders or ACOS.",
        KeywordArgs,
        _keywords,
    ),
    ToolDef(
        "get_anomalies",
        "Sustained shifts in conversion rate, CPC or spend over the last 14 mature days, with "
        "onset date, severity and status.",
        NoArgs,
        _anomalies,
    ),
    ToolDef(
        "get_account_forecast",
        "The most recently stored 14-day forecast of ad spend, ad sales and derived ACOS, with the "
        "model chosen and why. Says so when none exists.",
        NoArgs,
        _forecast,
    ),
)
REGISTRY: dict[str, ToolDef] = {t.name: t for t in (*TOOLS, KNOWLEDGE_TOOL)}


def tool_specs(*, knowledge: bool = False) -> list[ToolSpec]:
    """The knowledge tool is offered only when a knowledge base and embedder are configured."""
    return [t.spec() for t in (*TOOLS, *((KNOWLEDGE_TOOL,) if knowledge else ()))]


def _canonical(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"))


class ToolRunner:
    """Executes tool calls for one assistant run: validates, dedupes, budgets, tags evidence."""

    def __init__(self, ctx: ToolContext, *, max_calls: int = 8) -> None:
        self._ctx = ctx
        self._max_calls = max_calls
        self._cache: dict[tuple[str, str], Evidence | ToolError] = {}
        self._executed = 0
        self.evidence: list[Evidence] = []
        self.cache_hits = 0

    async def run(self, call: ToolCall) -> Evidence | ToolError:
        tool = REGISTRY.get(call.name)
        if tool is None or (tool is KNOWLEDGE_TOOL and not self._ctx.has_knowledge):
            return ToolError("unknown_tool", f"There is no tool named {call.name!r}.")
        try:
            args = tool.args.model_validate(call.arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
                for e in exc.errors()
            )
            return ToolError("invalid_arguments", problems)

        key = (call.name, _canonical(args.model_dump()))
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        if self._executed >= self._max_calls:
            return ToolError(
                "tool_budget_exhausted", "No further tool calls are allowed in this answer."
            )
        self._executed += 1
        outcome = await self._execute(tool, args, key)
        self._cache[key] = outcome
        return outcome

    async def _execute(
        self, tool: ToolDef, args: _Args, key: tuple[str, str]
    ) -> Evidence | ToolError:
        try:
            out = await tool.run(self._ctx, args)
        except NoDataError:
            return ToolError("no_data", "This account has no advertising data.")
        except NoMatureDataError:
            return ToolError(
                "no_mature_data",
                "Every day in that window is still inside the attribution window; "
                "ask for a longer one.",
            )
        except (InsufficientHistoryError, NoTrafficError) as exc:
            return ToolError("insufficient_history", str(exc))
        except (KnowledgeUnavailableError, LLMError):
            # An embedding outage or a missing knowledge base must not fail the whole answer.
            return ToolError(
                "knowledge_unavailable",
                "The reference documents cannot be searched right now. Answer from the account "
                "data only and say that definitions could not be looked up.",
            )

        flags: list[str] = []
        data = to_jsonable(out.result)
        if isinstance(data, dict):
            for k in ("provenance", "freshness", *out.drop):
                data.pop(k, None)
        data = add_rupees(scrub(data, flags))
        fitted = fit(data)
        if fitted is None:
            return ToolError("result_too_large", "The result is too large; narrow the request.")
        data, truncated = fitted
        prov, fresh = out.provenance, out.freshness
        sources = tuple(sorted(s.value for s in prov.data_sources)) if prov else ("KNOWLEDGE_BASE",)
        evidence = Evidence(
            id=f"ev_{len(self.evidence) + 1}",
            tool=tool.name,
            arguments=json.loads(key[1]),
            data=data,
            data_sources=sources,
            synthetic="MOCK" in sources,
            as_of=prov.as_of if prov else None,
            latest_date=fresh.latest_date if fresh else None,
            mature_through=fresh.mature_through if fresh else None,
            period=(out.period.start, out.period.end) if out.period else None,
            truncated=truncated,
            flags=tuple(flags),
        )
        self.evidence.append(evidence)
        return evidence
