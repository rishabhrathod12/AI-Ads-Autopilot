"""JSON serialisation for analytics results.

Conventions (docs/API_DESIGN.md): money is integer paise; ratios and percentages are numbers,
``null`` when undefined (never 0); dates are ISO 8601; every response carries ``meta`` saying where
the numbers came from and how fresh they are.
"""

from typing import Any

from app.application.jsonable import to_jsonable
from app.application.ports.performance_reader import Freshness, Provenance
from app.domain.ads.models import DataSource

MOCK_NOTICE = "MOCK data: synthetic, not live Amazon data."


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


__all__ = ["MOCK_NOTICE", "envelope", "meta", "to_jsonable"]
