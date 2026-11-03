"""Faithfulness validator: every number and date an answer states must trace to its evidence.

This is code, not a prompt: a model that is told "do not invent numbers" will sometimes do it
anyway, so every answer is checked before anyone sees it. The rules are deliberately strict:

* A number counts only if it appears in a *cited* evidence record (or a tool argument such as
  "28 days", or the length of a listed collection).
* It must match as written: rounded half-up or truncated to the digits stated, in the same unit
  (rupees, %, percentage points, x) and with the same sign when a sign is written.
* Figures the model derived itself (sums, ratios, differences) are NOT supported. The assistant
  must quote what a tool computed or say it cannot verify.
* Dates must match a date in the evidence. Number words (two..twenty) are checked too, so spelling
  a number out is not a loophole.

Known limit: this checks magnitudes, units and signs, not meaning ("rose" vs "fell" is judged by the
evaluation set in step 7, not here).
"""

import contextlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any, Literal

from app.application.assistant.evidence import Evidence

# ---- facts: the numbers and dates an evidence record supports --------------------------------

POINTS_KEYS = frozenset({"absolute", "change_points", "contribution_points"})
TEXT_MARK = "#text"  # a number read out of reference prose, not from a typed field


@dataclass(frozen=True)
class Fact:
    value: Decimal
    path: str  # dotted path inside the evidence, e.g. "data.kpis.acos_pct"

    @property
    def leaf(self) -> str:
        return self.path.rsplit(".", 1)[-1]


@dataclass(frozen=True)
class Facts:
    source_id: str
    numbers: tuple[Fact, ...]
    dates: frozenset[date]


_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _walk(obj: Any, path: str, out: list[Fact], dates: set[date]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, int | float):
        out.append(Fact(Decimal(repr(obj)), path))
    elif isinstance(obj, str):
        if path.endswith(".passage"):
            # Reference text may be quoted ("a 30% target"): its numbers are facts, with no unit
            # class, since prose does not say which of our unit fields it corresponds to.
            for s in extract_numbers(obj):
                signed = s.value * s.scale * (s.sign or 1)
                out.append(Fact(signed, f"{path}{TEXT_MARK}"))
        for m in _ISO_DATE.finditer(obj):
            with contextlib.suppress(ValueError):
                dates.add(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _walk(v, f"{path}.{k}", out, dates)
    elif isinstance(obj, list | tuple):
        out.append(Fact(Decimal(len(obj)), f"{path}#len"))  # "12 campaigns" is a countable fact
        for v in obj:
            _walk(v, path, out, dates)


def facts_from_evidence(ev: Evidence) -> Facts:
    numbers: list[Fact] = []
    dates: set[date] = set()
    _walk(ev.data, "data", numbers, dates)
    _walk(ev.arguments, "arguments", numbers, dates)
    for d in (ev.latest_date, ev.mature_through, *(ev.period or ())):
        if d is not None:
            dates.add(d)
    if ev.as_of is not None:
        dates.add(ev.as_of.date())
    return Facts(ev.id, tuple(numbers), frozenset(dates))


# ---- reading numbers and dates out of prose ---------------------------------------------------

_MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTHS = {m: i + 1 for i, m in enumerate(_MONTH_NAMES)}
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DAY_MONTH = re.compile(rf"(?i)\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH})(?:,?\s+(\d{{4}}))?\b")
_MONTH_DAY = re.compile(rf"(?i)\b({_MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b")
_IDENT = re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)+\b|\bev_\d+\b|\bkb_\d+\b|\b\d{1,2}:\d{2}\b")
# Characters people use when writing numbers: minus sign, en dash, times sign, rupee sign.
MINUS, EN_DASH, TIMES, RUPEE = chr(0x2212), chr(0x2013), chr(0xD7), chr(0x20B9)
_NUMBER = re.compile(
    rf"(?P<sign>[+\-{MINUS}{EN_DASH}])?\s?(?P<cur>{RUPEE}|Rs\.?|INR)?\s?(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?(?P<scale>lakhs?|crores?|k|m|million|bn)\b)?"
    rf"\s?(?P<unit>%|percentage\s+points?|pts?\b|points?\b|x\b|{TIMES})?",
    re.IGNORECASE,
)
_SCALES = {
    "lakh": 10**5,
    "lakhs": 10**5,
    "crore": 10**7,
    "crores": 10**7,
    "k": 10**3,
    "m": 10**6,
    "million": 10**6,
    "bn": 10**9,
}
_NUMBER_WORDS = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty",
]  # fmt: skip
_WORDS = {w: i for i, w in enumerate(_NUMBER_WORDS)}
# "one" is too common as a pronoun ("no one", "one-off"); zero is rarely a claim.
_CHECKED_WORDS = re.compile(
    r"(?i)\b(" + "|".join(w for w in _WORDS if w not in ("zero", "one")) + r")\b"
)
_SIGNS = {"+": 1, "-": -1, MINUS: -1, EN_DASH: -1}
Unit = Literal["none", "rupees", "percent", "points", "times"]


@dataclass(frozen=True)
class Stated:
    text: str
    value: Decimal  # as written, before scaling
    places: int
    scale: int
    unit: Unit
    sign: int | None  # +1 / -1 if explicitly written
    word: bool = False


@dataclass(frozen=True)
class StatedDate:
    text: str
    day: int
    month: int
    year: int | None


def extract_dates(text: str) -> tuple[list[StatedDate], str]:
    """(dates mentioned, text with those dates blanked out so their digits are not read as data)."""
    found: list[StatedDate] = []

    def add_iso(m: re.Match[str]) -> str:
        found.append(StatedDate(m.group(0), int(m.group(3)), int(m.group(2)), int(m.group(1))))
        return " "

    def add_named(day: str, month: str, year: str | None, whole: str) -> str:
        number = _MONTHS.get(month.lower().rstrip(".")[:3])
        if number is None or not 1 <= int(day) <= 31:
            return whole
        found.append(StatedDate(whole, int(day), number, int(year) if year else None))
        return " "

    text = _ISO_DATE.sub(add_iso, text)
    text = _DAY_MONTH.sub(lambda m: add_named(m.group(1), m.group(2), m.group(3), m.group(0)), text)
    text = _MONTH_DAY.sub(lambda m: add_named(m.group(2), m.group(1), m.group(3), m.group(0)), text)
    return found, text


def _unit(raw: str | None, currency: str | None) -> Unit:
    if currency:
        return "rupees"
    if not raw:
        return "none"
    r = raw.lower()
    if r == "%":
        return "percent"
    if r in ("x", TIMES):
        return "times"
    return "points"  # pt, pts, point(s), percentage point(s)


def extract_numbers(text: str) -> list[Stated]:
    """Numbers (digits, and spelled-out two..twenty) that are not dates or identifiers."""
    _, text = extract_dates(text)
    text = _IDENT.sub(" ", text)
    out: list[Stated] = []
    for m in _NUMBER.finditer(text):
        raw = m.group("num").replace(",", "")
        if re.fullmatch(r"(19|20)\d{2}", raw) and not m.group("unit") and not m.group("cur"):
            continue  # a bare year
        sign = _SIGNS.get(m.group("sign") or "")
        scale = _SCALES.get((m.group("scale") or "").lower(), 1)
        places = len(raw.split(".")[1]) if "." in raw else 0
        unit = _unit(m.group("unit"), m.group("cur"))
        out.append(Stated(m.group(0).strip(), Decimal(raw), places, scale, unit, sign))
    for w in _CHECKED_WORDS.finditer(text):
        value = Decimal(_WORDS[w.group(1).lower()])
        out.append(Stated(w.group(0), value, 0, 1, "none", None, word=True))
    return out


# ---- matching --------------------------------------------------------------------------------


def _unit_ok(unit: Unit, fact: Fact) -> bool:
    parts = fact.path.split(".")
    leaf = fact.leaf
    if unit == "none" or leaf.endswith(TEXT_MARK):
        return True
    if unit == "rupees":
        return any(p.endswith("_rupees") for p in parts)
    if unit == "points":
        return leaf in POINTS_KEYS
    if unit == "percent":
        return leaf not in POINTS_KEYS and any(p.endswith("_pct") for p in parts)
    return any(p == "roas" or p.endswith("_roas") for p in parts)  # "times"


def _magnitude_ok(s: Stated, fact: Fact) -> bool:
    if (
        s.unit == "none"
        and s.scale == 1
        and s.places == 0
        and fact.value != fact.value.to_integral_value()
    ):
        # A bare whole number ("5 campaigns") must match a whole-number fact exactly. Rounding
        # would let almost any small integer through (5.1483 "rounds" to 5), which is no check.
        return False
    step = Decimal(1).scaleb(-s.places)
    x = abs(fact.value) / Decimal(s.scale)
    return s.value in (x.quantize(step, ROUND_HALF_UP), x.quantize(step, ROUND_DOWN))


def _sign_ok(s: Stated, fact: Fact) -> bool:
    if s.sign is None or fact.value == 0:
        return True
    return (fact.value > 0) == (s.sign > 0)


def supporting(s: Stated, facts: Iterable[Facts]) -> bool:
    return any(
        _unit_ok(s.unit, f) and _magnitude_ok(s, f) and _sign_ok(s, f)
        for fs in facts
        for f in fs.numbers
    )


def date_supported(d: StatedDate, facts: Iterable[Facts]) -> bool:
    return any(
        x.day == d.day and x.month == d.month and (d.year is None or x.year == d.year)
        for fs in facts
        for x in fs.dates
    )


# ---- the answer ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    text: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftAnswer:
    claims: tuple[Claim, ...]
    caveats: tuple[str, ...] = ()
    confidence: Literal["high", "medium", "low"] = "medium"


ViolationKind = Literal[
    "unsupported_number", "unsupported_date", "unknown_evidence", "uncited_numbers"
]
_UNIT_NAMES = {
    "rupees": "rupee amount",
    "percent": "percentage",
    "points": "percentage-point change",
    "times": "multiple (x)",
    "none": "number",
}


@dataclass(frozen=True)
class Violation:
    kind: ViolationKind
    where: str  # "claim 2", "caveat 1"
    text: str
    detail: str


@dataclass(frozen=True)
class ValidationResult:
    violations: tuple[Violation, ...] = ()
    numbers_checked: int = 0
    dates_checked: int = 0
    cited: frozenset[str] = field(default_factory=frozenset)

    @property
    def ok(self) -> bool:
        return not self.violations


def _echo_filters(question: str) -> tuple[list[Stated], list[StatedDate]]:
    """Bare counts and dates the seller wrote in the question itself ("next two weeks", "14 days").

    Only unit-less numbers qualify. A rupee, percent or multiple amount in the question is a
    figure the seller asserts, so repeating it still needs evidence (ADR 0019).
    """
    dates, _ = extract_dates(question)
    return [n for n in extract_numbers(question) if n.unit == "none"], dates


def validate_answer(
    draft: DraftAnswer, evidence: Sequence[Evidence], *, question: str = ""
) -> ValidationResult:
    """Check every claim against the evidence it cites, and every caveat against all evidence.

    Unit-less numbers and dates that appear in ``question`` are the seller's own words repeated
    back, not claims about the data, so they are not checked.
    """
    echo_numbers, echo_dates = _echo_filters(question)
    by_id = {e.id: facts_from_evidence(e) for e in evidence}
    everything = list(by_id.values())
    violations: list[Violation] = []
    cited: set[str] = set()
    counts = {"numbers": 0, "dates": 0}

    def check(text: str, pool: list[Facts], where: str, *, require_citation: bool) -> None:
        dates, _ = extract_dates(text)
        numbers = extract_numbers(text)
        dates = [
            d
            for d in dates
            if not any((d.day, d.month, d.year) == (q.day, q.month, q.year) for q in echo_dates)
        ]
        numbers = [
            n
            for n in numbers
            if not (
                n.unit == "none"
                and any(q.value == n.value and q.scale == n.scale for q in echo_numbers)
            )
        ]
        if require_citation and (dates or numbers) and not pool:
            detail = "This statement contains figures or dates but cites no evidence."
            violations.append(Violation("uncited_numbers", where, text, detail))
            return
        for d in dates:
            counts["dates"] += 1
            if not date_supported(d, pool):
                detail = "No cited evidence contains this date."
                violations.append(Violation("unsupported_date", where, d.text, detail))
        for s in numbers:
            counts["numbers"] += 1
            if not supporting(s, pool):
                detail = (
                    f"No cited evidence contains this {_UNIT_NAMES[s.unit]} "
                    "(as written, same unit and sign)."
                )
                violations.append(Violation("unsupported_number", where, s.text, detail))

    for i, claim in enumerate(draft.claims, start=1):
        pool: list[Facts] = []
        for eid in claim.evidence_ids:
            if eid not in by_id:
                detail = "This evidence id does not exist."
                violations.append(Violation("unknown_evidence", f"claim {i}", eid, detail))
            else:
                pool.append(by_id[eid])
                cited.add(eid)
        check(claim.text, pool, f"claim {i}", require_citation=True)

    for i, caveat in enumerate(draft.caveats, start=1):
        check(caveat, everything, f"caveat {i}", require_citation=False)

    return ValidationResult(tuple(violations), counts["numbers"], counts["dates"], frozenset(cited))


def correction_message(result: ValidationResult) -> str:
    """What to tell the model so it can fix its answer: specific, short, and never a number."""
    lines = ["Your answer could not be verified. Fix every item below, then answer again:"]
    for v in result.violations:
        lines.append(f"- {v.where}: {v.text!r}: {v.detail}")
    lines.append(
        "Use only figures and dates exactly as returned by the tools (rounded as you like, same "
        "unit), cite the evidence id for every claim that contains one, and do not calculate new "
        "figures. If the data does not support a statement, remove it or say you cannot verify it."
    )
    return "\n".join(lines)
