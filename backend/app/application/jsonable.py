"""JSON conversion shared by the API and the assistant's tools.

Conventions: money stays integer paise; Decimals become numbers rounded to 4 places for display;
dates are ISO 8601; computed properties of dataclasses are included.
"""

import inspect
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

DECIMAL_PLACES = 4  # presentation only; calculations stay exact Decimals until here


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
