"""Turn a finished run into the JSON stored with it. Shared by every store implementation."""

from typing import Any

from app.application.assistant.loop import AssistantRun
from app.application.jsonable import to_jsonable


def answer_record(run: AssistantRun) -> dict[str, Any]:
    return {
        "text": run.answer_text,
        "claims": [{"text": c.text, "evidence_ids": list(c.evidence_ids)} for c in run.claims],
        "caveats": list(run.caveats),
        "confidence": run.confidence,
        "system_notes": list(run.system_notes),
        "dropped_claims": run.dropped_claims,
        "cited": sorted(run.cited),
    }


def evidence_records(run: AssistantRun) -> list[dict[str, Any]]:
    return [to_jsonable(e) for e in run.evidence]


def trace_records(run: AssistantRun) -> list[dict[str, Any]]:
    return [to_jsonable(s) for s in run.steps]


def violation_records(run: AssistantRun) -> list[dict[str, Any]]:
    return [to_jsonable(v) for v in run.violations]
