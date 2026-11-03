"""The faithfulness validator: numbers and dates must trace to cited evidence, as written."""

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.application.assistant.evidence import Evidence
from app.application.assistant.faithfulness import (
    Claim,
    DraftAnswer,
    correction_message,
    extract_dates,
    extract_numbers,
    facts_from_evidence,
    validate_answer,
)
from app.application.assistant.tools import ToolContext, ToolRunner
from app.application.ports.llm import ToolCall
from app.infrastructure.providers.mock.generator import MockConfig, generate
from tests.unit.fake_reader import FakeReader

NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
D = Decimal


def ev(data: dict[str, Any], eid: str = "ev_1", **over: Any) -> Evidence:
    base: dict[str, Any] = {
        "id": eid,
        "tool": "get_dashboard_summary",
        "arguments": {"days": 28},
        "data": data,
        "data_sources": ("MOCK",),
        "synthetic": True,
        "as_of": NOW,
        "latest_date": date(2026, 11, 1),
        "mature_through": date(2026, 10, 29),
        "period": (date(2026, 10, 5), date(2026, 11, 1)),
    }
    return Evidence(**{**base, **over})


SUMMARY = ev(
    {
        "totals": {
            "spend_paise": 17793152,
            "spend_rupees": 177931.52,
            "clicks": 11672,
            "orders": 1010,
        },
        "kpis": {"acos_pct": 19.4239, "roas": 5.1483, "cpc_rupees": 15.24},
        "immature_days": 3,
        "comparison": {
            "changes": {
                "acos_pct": {
                    "current": 19.4,
                    "previous": 17.7,
                    "absolute": 1.74,
                    "relative_pct": 9.8,
                },
                "cvr_pct": {
                    "current": 8.2,
                    "previous": 9.4,
                    "absolute": -1.2,
                    "relative_pct": -12.8,
                },
            },
            "acos_explanation": {
                "change_points": 1.74,
                "drivers": [{"name": "cvr", "contribution_points": 1.6}],
            },
        },
        "rows": [{"k": 101}, {"k": 202}, {"k": 303}],
    }
)


def says(text: str, *evidence: Evidence, cite: tuple[str, ...] = ("ev_1",)) -> list[str]:
    """Violation details for one claim, so tests read as 'does this sentence pass?'."""
    result = validate_answer(DraftAnswer((Claim(text, cite),)), list(evidence or [SUMMARY]))
    return [f"{v.kind}:{v.text}" for v in result.violations]


# ---- reading numbers -------------------------------------------------------------------------
def test_numbers_units_signs_and_scales_are_read_as_written() -> None:
    by_text = {
        s.text: s
        for s in extract_numbers(
            "₹1,77,932 and 19.4% and +1.7 pts and −4.2% and 5.15x and 1.8 lakh"
        )
    }
    assert by_text["₹1,77,932"].unit == "rupees"
    assert by_text["₹1,77,932"].value == D(177932)
    assert (by_text["19.4%"].unit, by_text["19.4%"].places) == ("percent", 1)
    assert (by_text["+1.7 pts"].unit, by_text["+1.7 pts"].sign) == ("points", 1)
    assert by_text["−4.2%"].sign == -1
    assert by_text["5.15x"].unit == "times"
    assert (by_text["1.8 lakh"].scale, by_text["1.8 lakh"].value) == (100000, D("1.8"))


def test_the_multiplication_sign_counts_as_x() -> None:
    (s,) = extract_numbers("ROAS was 5.15×")
    assert s.unit == "times"


def test_identifiers_dates_and_bare_years_are_not_numbers() -> None:
    assert extract_numbers("MOCK-CMP-001 and MOCK-TGT-00012 per ev_3 at 06:00") == []
    assert extract_numbers("In 2026 the account grew") == []
    dates, rest = extract_dates("From 5 Oct to 1 Nov 2026, and Oct 21, plus 2026-10-29")
    assert [(d.day, d.month, d.year) for d in dates] == [
        (29, 10, 2026),
        (5, 10, None),
        (1, 11, 2026),
        (21, 10, None),
    ]
    assert not any(ch.isdigit() for ch in rest)


def test_spelled_out_numbers_are_checked_but_one_is_left_alone() -> None:
    words = [
        s.text.lower()
        for s in extract_numbers(
            "Three campaigns and twenty keywords, no one knows; a one-off promotion"
        )
    ]
    assert words == ["three", "twenty"]


# ---- matching: rounding ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "text", ["ACOS was 19.4%", "ACOS was 19.42%", "ACOS was 19%", "ACOS was 19.4239%"]
)
def test_values_rounded_or_truncated_to_the_stated_digits_pass(text: str) -> None:
    assert says(text) == []


@pytest.mark.parametrize(
    "text", ["ACOS was 20%", "ACOS was 19.5%", "ACOS was 19.3%", "ACOS was 19.43%", "ACOS was 21%"]
)
def test_values_that_do_not_round_to_the_evidence_fail(text: str) -> None:
    assert says(text) == [f"unsupported_number:{text.split()[-1]}"]


def test_half_up_rounding_and_truncation_are_both_accepted() -> None:
    e = ev({"x_pct": 19.46})
    assert says("It was 19.5%", e) == []  # rounded
    assert says("It was 19.4%", e) == []  # truncated
    assert says("It was 19.6%", e) != []


# ---- matching: units -------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Spend was ₹1,77,932", True),
        ("Spend was ₹177,932", True),  # western grouping reads the same
        ("Spend was 177,932%", False),  # rupees are not a percentage
        ("CPC was ₹15.24", True),
        ("ACOS was ₹19.4", False),  # a percentage is not rupees
        ("ACOS rose 1.7 points", True),
        ("ACOS rose 1.7%", False),  # 1.74 is a change in points, not a relative percent
        ("Conversion fell 12.8%", True),  # relative_pct is a percent
        ("Conversion fell 12.8 points", False),
        ("ROAS was 5.15x", True),
        ("ROAS was 5.15%", False),
        ("ACOS was 19.4 times", True),  # 'times' is prose here, not the unit marker
    ],
)
def test_the_unit_must_match_the_kind_of_figure(text: str, ok: bool) -> None:
    assert (says(text) == []) is ok


# ---- matching: signs and scales --------------------------------------------------------------
def test_an_explicit_sign_must_match_the_evidence() -> None:
    assert says("ACOS moved +1.7 points") == []
    assert says("ACOS moved -1.7 points") != []
    assert says("Conversion changed −1.2 points") == []  # evidence is negative
    assert says("Conversion changed +1.2 points") != []
    assert says("Conversion fell 1.2 points") == []  # unsigned: direction is written in words


def test_lakh_and_crore_scales_are_understood() -> None:
    assert says("Spend was about ₹1.8 lakh") == []
    assert says("Spend was about ₹2 lakh") == []
    assert says("Spend was about ₹1.9 lakh") != []
    assert says("Spend was about ₹0.02 crore") == []


# ---- matching: counts, arguments, derived figures ---------------------------------------------
def test_counts_of_listed_items_and_tool_arguments_are_supported() -> None:
    assert says("Across 3 rows over the last 28 days") == []
    assert says("Across 4 rows") != []
    assert says("Over the last 30 days") != []
    assert says("There are three rows") == []
    assert says("There are two rows") != []  # spelling it out is not a loophole


def test_figures_the_model_computed_itself_are_not_supported() -> None:
    # 1010 orders / 11672 clicks is a real conversion rate (8.65%) but no tool said so here.
    assert says("Conversion was 8.7%") != []
    # a sum of two evidence figures is not evidence either
    assert says("Clicks plus orders were 12,682") != []


# ---- dates -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("From 5 Oct to 1 Nov", True),
        ("Complete through 29 Oct", True),
        ("Complete through Oct 29", True),
        ("Complete through 2026-10-29", True),
        ("Complete through 29 Oct 2026", True),
        ("Complete through 29 Oct 2025", False),  # wrong year
        ("It started on 21 Oct", False),  # no such date in the evidence
        ("Loaded on 1 Nov 2026", True),  # as_of date
    ],
)
def test_dates_must_appear_in_the_evidence(text: str, ok: bool) -> None:
    assert (says(text) == []) is ok


# ---- whole answers -----------------------------------------------------------------------------
def test_a_fully_faithful_answer_passes_and_reports_what_it_checked() -> None:
    draft = DraftAnswer(
        claims=(
            Claim("Ad spend was ₹1,77,932 over the last 28 days.", ("ev_1",)),
            Claim("ACOS was 19.4%, up 1.7 points, mainly because of conversion rate.", ("ev_1",)),
            Claim("Campaign names are shown in the table.", ()),  # no figures: no citation needed
        ),
        caveats=("The latest 3 days are still maturing.", "This is MOCK data."),
    )
    result = validate_answer(draft, [SUMMARY])
    assert result.ok
    assert result.violations == ()
    assert result.cited == {"ev_1"}
    assert result.numbers_checked == 5  # 1,77,932; 28; 19.4%; 1.7 points; 3


def test_a_claim_with_figures_but_no_citation_is_rejected() -> None:
    r = validate_answer(DraftAnswer((Claim("ACOS was 19.4%.", ()),)), [SUMMARY])
    assert [v.kind for v in r.violations] == ["uncited_numbers"]


def test_citing_an_unknown_evidence_id_is_rejected() -> None:
    r = validate_answer(DraftAnswer((Claim("ACOS was 19.4%.", ("ev_9",)),)), [SUMMARY])
    kinds = sorted(v.kind for v in r.violations)
    assert kinds == ["uncited_numbers", "unknown_evidence"]


def test_a_figure_must_come_from_the_evidence_the_claim_actually_cites() -> None:
    other = ev({"kpis": {"acos_pct": 55.5}}, "ev_2")
    r = validate_answer(DraftAnswer((Claim("ACOS was 19.4%.", ("ev_2",)),)), [SUMMARY, other])
    assert [v.kind for v in r.violations] == ["unsupported_number"]  # true in ev_1, but not cited
    ok = validate_answer(
        DraftAnswer((Claim("ACOS was 19.4%.", ("ev_1", "ev_2")),)), [SUMMARY, other]
    )
    assert ok.ok


def test_caveats_are_checked_against_all_evidence_without_needing_citations() -> None:
    assert validate_answer(DraftAnswer((), ("3 days are still maturing.",)), [SUMMARY]).ok
    bad = validate_answer(DraftAnswer((), ("Sales are up 77%.",)), [SUMMARY])
    assert [(v.kind, v.where) for v in bad.violations] == [("unsupported_number", "caveat 1")]


def test_violations_say_where_they_are() -> None:
    draft = DraftAnswer(
        (Claim("ACOS was 19.4%.", ("ev_1",)), Claim("Spend was ₹9,99,999.", ("ev_1",)))
    )
    (v,) = validate_answer(draft, [SUMMARY]).violations
    assert v.where == "claim 2"
    assert v.text == "₹9,99,999"
    assert "rupee amount" in v.detail


def test_the_correction_message_names_every_problem_and_says_how_to_fix_it() -> None:
    draft = DraftAnswer((Claim("Spend was ₹9,99,999 on 3 Dec.", ("ev_1",)),))
    msg = correction_message(validate_answer(draft, [SUMMARY]))
    assert "₹9,99,999" in msg
    assert "3 Dec" in msg
    assert "do not calculate new figures" in msg
    assert "cite the evidence id" in msg


def test_facts_include_list_lengths_arguments_and_dates() -> None:
    facts = facts_from_evidence(SUMMARY)
    paths = {f.path for f in facts.numbers}
    assert "data.rows#len" in paths
    assert "arguments.days" in paths
    assert date(2026, 10, 29) in facts.dates
    assert date(2026, 11, 1) in facts.dates  # also from as_of


# ---- mutation checks against real tool evidence ---------------------------------------------
async def test_numbers_quoted_from_real_tool_output_pass_and_perturbed_ones_fail() -> None:
    data = generate(MockConfig(end_date=date(2026, 10, 31), now=NOW))
    runner = ToolRunner(ToolContext(FakeReader(data, UUID(int=5), NOW), _NoForecasts()))
    real = await runner.run(ToolCall("c", "get_dashboard_summary", {"days": 28}))
    assert isinstance(real, Evidence)
    totals, kpis = real.data["totals"], real.data["kpis"]

    truthful = (
        f"Ad spend was ₹{totals['spend_rupees']:,.0f}, ad sales ₹{totals['sales_rupees']:,.0f}, "
        f"ACOS {kpis['acos_pct']:.1f}% and ROAS {kpis['roas']:.2f}x over {totals['clicks']:,} clicks."
    )
    assert validate_answer(DraftAnswer((Claim(truthful, (real.id,)),)), [real]).ok

    for factor in (0.9, 0.97, 1.03, 1.1, 2.0):
        spend = totals["spend_rupees"] * factor
        text = f"Ad spend was ₹{spend:,.0f}."
        assert not validate_answer(DraftAnswer((Claim(text, (real.id,)),)), [real]).ok, factor
        acos = f"ACOS was {kpis['acos_pct'] * factor:.1f}%."
        assert not validate_answer(DraftAnswer((Claim(acos, (real.id,)),)), [real]).ok, factor


class _NoForecasts:
    async def save(self, run: Any) -> UUID:
        raise AssertionError

    async def get(self, run_id: UUID) -> None:
        return None

    async def latest(self, *a: Any) -> None:
        return None


@given(
    v=st.decimals(min_value=D("0.01"), max_value=D("9999999"), places=4), places=st.integers(0, 3)
)
def test_any_value_written_to_its_stated_precision_is_accepted(v: Decimal, places: int) -> None:
    written = v.quantize(D(1).scaleb(-places), ROUND_HALF_UP)
    assert says(f"It was {written}%", ev({"x_pct": float(v)})) == []


@given(
    v=st.decimals(min_value=D("100"), max_value=D("9999999"), places=2), places=st.integers(0, 2)
)
def test_a_value_ten_percent_off_is_always_rejected(v: Decimal, places: int) -> None:
    wrong = (v * D("1.1")).quantize(D(1).scaleb(-places), ROUND_HALF_UP)
    assert says(f"It was {wrong}%", ev({"x_pct": float(v)})) != []


def test_only_dates_in_the_period_window_are_not_blindly_allowed() -> None:
    # the period is 5 Oct - 1 Nov; a date inside it that is NOT an endpoint is still unsupported
    assert says("Spend peaked on 15 Oct") != []
    assert (date(2026, 10, 15) - timedelta(days=0)).day == 15
