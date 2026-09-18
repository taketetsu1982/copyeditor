import json
from copy import deepcopy

from .diagnosis import Diagnosis, DiagnosticItem, check_no_issue, parse_diagnoses
from .providers.base import GenerationResult, Usage
from .requests import ValidationError
from .responses import Candidate, output_schema, valid, validate_final

BUDGET_MESSAGE = "Request processing budget exhausted; preserve the original."


def rewrite_output_schema(*, limits=True):
    schema = output_schema("polish_text")
    body = deepcopy(schema["oneOf"][0]["properties"]["text"])
    body.pop("maxLength")
    def field(maximum):
        return {**body, **({"maxLength": maximum} if limits else {})}
    diagnosis = {"oneOf": [
        {"type": "object", "properties": {"status": {"const": "issue"},
         "expression": field(160), "reason": field(320)},
         "required": ["status", "expression", "reason"], "additionalProperties": False},
        {"type": "object", "properties": {"status": {"const": "no_issue"},
         "expression": {"type": "null"}, "reason": {"type": "null"}},
         "required": ["status", "expression", "reason"], "additionalProperties": False}]}
    for variant in schema["oneOf"]:
        properties = variant["properties"]
        properties["schema_version"] = {"type": "integer", "const": 2}
        properties["degree"] = {"const": "rewrite"}
        variant["required"].append("degree")
        properties["model_calls"]["maximum"] = 17
        if "error" in properties:
            errors = properties["error"]["oneOf"]
            budget = deepcopy(errors[-1])
            budget["properties"]["code"] = {"const": "request_budget"}
            budget["properties"]["message"] = {"const": BUDGET_MESSAGE}
            errors.append(budget)
            for error in errors:
                field_schema = error["properties"]["field"]
                if "anyOf" in field_schema:
                    field_schema["anyOf"].append({"const": "degree"})
        else:
            target = properties["items"]["items"] if "items" in properties else variant
            target["properties"]["diagnosis"] = deepcopy(diagnosis)
            target["required"].append("diagnosis")
    return schema


def validate_rewrite_final(payload, originals=()):
    # Complete integrity, including metadata, precedes all diagnostic/body caps.
    validate_final(payload, schema=rewrite_output_schema(limits=False), rewrite=True, check_limits=False)
    if payload["status"] == "ok":
        entries = payload.get("items", [{**payload, "id": "text"}])
        valid(bool(originals) and [item["id"] for item in entries] == [item.id for item in originals])
        batches = (len(originals) + 3) // 4
        calls = payload["model_calls"]
        valid(1 + batches <= calls <= 1 + 2 * batches)
        retried = any(item["regenerated"] for item in entries)
        valid(retried == (calls > 1 + batches))
        diagnoses = tuple(DiagnosticItem(item["id"], Diagnosis(**item["diagnosis"])) for item in entries)
        candidates = tuple(Candidate(item["id"], item["text"], item["flag"]) for item in entries)
        check_no_issue(diagnoses, originals, candidates)
        valid(all(not item.flag or item.text == original.text for item, original in zip(candidates, originals)))
        raw = json.dumps({"diagnoses": [{"id": item.id, **item.diagnosis._asdict()} for item in diagnoses]})
        parse_diagnoses(GenerationResult(raw, "stop", Usage(None, None, None)), originals)
        if sum(len(item.text) for item in candidates) > 16000:
            raise ValidationError("output_limit")
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > 1048576:
        raise ValidationError("output_limit")
