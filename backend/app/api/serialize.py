"""JSON serialisation for analytics results.

Conventions (docs/API_DESIGN.md): money is integer paise; ratios and percentages are numbers,
``null`` when undefined (never 0); dates are ISO 8601; every response carries ``meta`` saying where
the numbers came from and how fresh they are.
"""

import inspect
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from app.application.ports.performance_reader import Freshness, Provenance
from app.domain.ads.models import DataSource

DECIMAL_PLACES = 4  # presentation only; calculations stay exact Decimals until here
MOCK_NOTICE = "MOCK data: synthetic, not live Amazon data."


def to_jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, bool | int | str):
        return obj
    if isinstance(obj, Decimal):
        return float(round(obj, DECIMAL_PLACES))
    if isinstance(obj, float):
        return round(obj, DECIMAL_PLACES)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        out = {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
        # Computed values (e.g. a change's absolute/relative size) are part of the contract too.
        for name, _ in inspect.getmembers(type(obj), lambda m: isinstance(m, property)):
            if not name.startswith("_"):
                out[name] = to_jsonable(getattr(obj, name))
        return out
    if isinstance(obj, dict):
        return {str(to_jsonable(k)): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set | frozenset):
        return [to_jsonable(v) for v in obj]
    raise TypeError(f"cannot serialise {type(obj).__name__}")


def meta(provenance: Provenance, freshness: Freshness) -> dict[str, Any]:
    sources = sorted(s.value for s in provenance.data_sources)
    return {
        "data_sources": sources,
        "synthetic": DataSource.MOCK.value in sources,
        "notice": MOCK_NOTICE if DataSource.MOCK.value in sources else None,
        "as_of": to_jsonable(provenance.as_of),
        "latest_date": to_jsonable(freshness.latest_date),
        "mature_through": to_jsonable(freshness.mature_through),
    }


def envelope(
    result: Any, provenance: Provenance, freshness: Freshness, *, drop: tuple[str, ...] = ()
) -> dict[str, Any]:
    """``{"meta": ..., "data": ...}``; provenance and freshness are lifted out into ``meta``."""
    data = to_jsonable(result)
    for key in ("provenance", "freshness", *drop):
        data.pop(key, None)
    return {"meta": meta(provenance, freshness), "data": data}
