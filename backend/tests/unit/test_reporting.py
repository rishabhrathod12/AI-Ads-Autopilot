"""The deterministic performance report: figures match the analytics, outputs are safe."""

import csv
import io
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.tenancy import Scope, get_scope
from app.application.analytics.campaigns import campaign_overview
from app.application.analytics.dashboard import dashboard_summary
from app.application.ports.performance_reader import CampaignInfo
from app.application.reporting.report import (
    CAMPAIGN_COLUMNS,
    build_performance_report,
    campaigns_csv,
    csv_safe,
    md_safe,
    ratio,
    report_markdown,
    rupees,
)
from app.config import Settings
from app.domain.kpi.metrics import Kpis
from app.domain.kpi.periods import DateRange
from app.infrastructure.providers.mock.generator import GeneratedData, MockConfig, generate
from tests.unit.fake_reader import FakeReader
from tests.unit.test_api_analytics import MemoryForecastStore

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
ORG = UUID("77777777-7777-4777-8777-777777777777")
PERIOD = DateRange(END - timedelta(days=27), END)


@pytest.fixture(scope="module")
def data() -> GeneratedData:
    return generate(MockConfig(end_date=END, now=NOW))


@pytest.fixture(scope="module")
def reader(data: GeneratedData) -> FakeReader:
    return FakeReader(data, ORG, NOW)


async def test_report_figures_equal_the_analytics(reader: FakeReader) -> None:
    report = await build_performance_report(reader, PERIOD)
    summary = await dashboard_summary(reader, PERIOD)
    overview = await campaign_overview(reader, PERIOD)
    assert summary.comparison is not None
    assert report.totals == summary.comparison.current_totals  # complete days, like the table
    assert report.kpis == Kpis.from_totals(report.totals)
    assert report.period == summary.comparison.current
    assert report.synthetic and report.data_sources == ("MOCK",)
    assert len(report.campaigns) == len(overview.rows)
    assert sum(c.spend_paise for c in report.campaigns) == report.totals.spend_paise
    assert sum(c.sales_paise for c in report.campaigns) == report.totals.sales_paise
    spends = [c.spend_paise for c in report.campaigns]
    assert spends == sorted(spends, reverse=True)


async def test_notes_say_mock_and_ad_attributed(reader: FakeReader) -> None:
    report = await build_performance_report(reader, PERIOD)
    text = " ".join(report.notes)
    assert "MOCK" in text and "ad-attributed" in text
    assert "attribution window" in text  # the latest days are excluded


async def test_csv_has_one_row_per_campaign_and_the_exact_columns(reader: FakeReader) -> None:
    report = await build_performance_report(reader, PERIOD)
    rows = list(csv.reader(io.StringIO(campaigns_csv(report))))
    assert tuple(rows[0]) == CAMPAIGN_COLUMNS
    assert len(rows) - 1 == len(report.campaigns)
    first = report.campaigns[0]
    assert rows[1][3] == rupees(first.spend_paise)


async def test_markdown_contains_every_section_and_no_changes_are_promised(
    reader: FakeReader,
) -> None:
    md = report_markdown(await build_performance_report(reader, PERIOD))
    for heading in (
        "# PPC performance report",
        "## Account",
        "## Campaigns",
        "## Keywords",
        "## Sustained",
    ):
        assert heading in md
    assert "changes nothing" in md


@pytest.mark.parametrize("evil", ['=HYPERLINK("http://x")', "+1+1", "-2+3", "@SUM(A1)"])
def test_csv_neutralises_formula_prefixes(evil: str) -> None:
    assert csv_safe(evil).startswith("'")
    assert csv_safe("safe name") == "safe name"
    assert "\n" not in csv_safe("a\nb") and "\x00" not in csv_safe("a\x00b")


def test_markdown_escapes_table_and_html_breaking_characters() -> None:
    out = md_safe("a|b <script> `x` *y* [z](u)")
    for raw in ("|", "<", "`", "*", "["):
        assert all(out[i - 1] == "\\" for i, ch in enumerate(out) if ch == raw)


def test_money_and_ratio_formatting_never_invents_a_zero() -> None:
    assert rupees(None) == "n/a" and ratio(None) == "n/a"
    assert rupees(123456) == "1234.56"
    assert rupees(5) == "0.05"
    assert ratio(Decimal("19.4239")) == "19.42"
    assert ratio(Decimal("2.345"), 2) == "2.35"  # half-up, not banker's rounding


async def test_a_campaign_named_like_a_formula_is_safe_in_every_format(
    data: GeneratedData,
) -> None:
    evil = '=cmd|"/c calc"!A1'

    class Renamed(FakeReader):
        async def campaigns(self) -> list[CampaignInfo]:
            return [replace(c, name=evil) for c in await super().campaigns()]

    report = await build_performance_report(Renamed(data, ORG, NOW), PERIOD)
    rows = list(csv.reader(io.StringIO(campaigns_csv(report))))[1:]
    assert rows and all(r[0] == "'" + evil for r in rows)
    assert "\\|" in report_markdown(report)  # the pipe in the name cannot split a table row


@pytest.fixture
def client(data: GeneratedData) -> Iterator[TestClient]:
    app = create_app(Settings(_env_file=None))
    reader, store = FakeReader(data, ORG, NOW), MemoryForecastStore()

    async def scope() -> AsyncIterator[Scope]:
        yield Scope(ORG, reader, store)

    app.dependency_overrides[get_scope] = scope
    with TestClient(app) as c:
        yield c


def test_api_json_markdown_and_csv(client: TestClient) -> None:
    js = client.get("/api/v1/reports/performance")
    assert js.status_code == 200
    body = js.json()
    assert body["data"]["synthetic"] is True
    assert body["data"]["campaigns"] and any("MOCK" in n for n in body["notes"])

    md = client.get("/api/v1/reports/performance", params={"format": "markdown"})
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "# PPC performance report" in md.text

    cs = client.get("/api/v1/reports/performance", params={"format": "csv"})
    assert cs.status_code == 200 and cs.headers["content-type"].startswith("text/csv")
    assert "attachment" in cs.headers["content-disposition"]
    assert cs.text.splitlines()[0] == ",".join(CAMPAIGN_COLUMNS)


def test_api_rejects_bad_format_and_reversed_dates(client: TestClient) -> None:
    assert client.get("/api/v1/reports/performance", params={"format": "pdf"}).status_code == 422
    r = client.get("/api/v1/reports/performance", params={"from": "2026-10-20", "to": "2026-10-01"})
    assert r.status_code == 422
