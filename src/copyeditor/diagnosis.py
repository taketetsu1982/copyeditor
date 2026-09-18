import json
from typing import NamedTuple

from .requests import ValidationError
from .responses import nonblank, reject_constant, unique_object, valid


class Diagnosis(NamedTuple):
    status: str
    expression: str | None
    reason: str | None


class DiagnosticItem(NamedTuple):
    id: str
    diagnosis: Diagnosis


def parse_diagnoses(result, expected_items):
    if result.finish != "stop":
        code = ("generation_truncated" if result.finish == "truncated" else
                "provider_error" if result.finish == "blocked" else "invalid_response")
        raise ValidationError(code)
    data = None
    if type(result.raw_json) is str:
        try:
            data = json.loads(result.raw_json, object_pairs_hook=unique_object, parse_constant=reject_constant)
        except (ValueError, RecursionError):
            pass
    # Do not retain decoder exceptions: their context contains the source text.
    valid(type(data) is dict and set(data) == {"diagnoses"})
    valid(type(data["diagnoses"]) is list and bool(data["diagnoses"]))
    originals = {item.id: item.text for item in expected_items}
    valid(len(originals) == len(expected_items))
    diagnoses = {}
    for item in data["diagnoses"]:
        valid(type(item) is dict and set(item) == {"id", "status", "expression", "reason"})
        identity = item["id"]
        valid(type(identity) is str and identity in originals and identity not in diagnoses)
        status, expression, reason = item["status"], item["expression"], item["reason"]
        valid(type(status) is str and status in ("issue", "no_issue"))
        if status == "issue":
            valid(nonblank(expression) and nonblank(reason))
            valid(expression in originals[identity])
        else:
            valid(expression is None and reason is None)
        diagnoses[identity] = DiagnosticItem(identity, Diagnosis(status, expression, reason))
    valid(set(diagnoses) == set(originals))
    ordered = tuple(diagnoses[item.id] for item in expected_items)
    # Check every item's integrity before applying any field or aggregate cap.
    if (any(len(item.diagnosis.expression or "") > 160 or len(item.diagnosis.reason or "") > 320
            for item in ordered)
            or sum(len(item.diagnosis.expression or "") + len(item.diagnosis.reason or "")
                   for item in ordered) > 8192):
        raise ValidationError("output_limit")
    return ordered


def check_no_issue(diagnoses, originals, candidates):
    """Check a candidate subset before flags or size limits, and again at merge."""
    fixed = {item.id: item.diagnosis for item in diagnoses}
    source = {item.id: item.text for item in originals}
    received = {item.id: item.text for item in candidates}
    valid(len(fixed) == len(diagnoses) and len(source) == len(originals))
    valid(len(received) == len(candidates) and set(received) == set(source))
    valid(set(source) <= set(fixed))
    for identity, text in received.items():
        if fixed[identity].status == "no_issue":
            valid(text == source[identity])
