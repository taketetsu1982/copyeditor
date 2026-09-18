import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from copyeditor.diagnosis import Diagnosis, DiagnosticItem
from copyeditor.providers.base import GenerationResult, SourceItem, Usage
from copyeditor.requests import MESSAGES, ValidationError
from copyeditor.responses import output_schema, parse_generation
from copyeditor.rewrite_response import BUDGET_MESSAGE, rewrite_output_schema, validate_rewrite_final
from tests.contracts.test_ctr01_responses import public_fixture


def fixture(count=1, text="Hello."):
    payload = public_fixture(dict(tool="polish_text", provider=[{}, {}], expect={"status": "ok"}, input={}))
    payload.update(schema_version=2, degree="rewrite", text=text,
                   diagnosis=dict(status="no_issue", expression=None, reason=None), model_calls=1 + (count + 3) // 4)
    originals = (SourceItem("text", text, ""),)
    if count > 1:
        keys = ("text", "flag", "regenerated", "protected_terms", "findings", "findings_truncated", "diagnosis")
        entry = {key: payload.pop(key) for key in keys}
        payload["items"] = [{"id": str(i), **deepcopy(entry)} for i in range(count)]
        originals = tuple(SourceItem(str(i), text, "") for i in range(count))
    return payload, originals


def reject(payload, originals=(), code="invalid_response"):
    with pytest.raises(ValidationError) as caught:
        validate_rewrite_final(payload, originals)
    assert caught.value.code == code


@pytest.mark.parametrize("count", [1, 4, 5, 32])
def test_ctr01_v2_complete_shapes_and_v1_discovery_unchanged(count):
    payload, originals = fixture(count)
    Draft202012Validator.check_schema(rewrite_output_schema())
    assert Draft202012Validator(rewrite_output_schema()).is_valid(payload)
    assert not Draft202012Validator(output_schema("polish_text")).is_valid(payload)
    validate_rewrite_final(payload, originals)


@pytest.mark.parametrize("key,value", [("schema_version", 1), ("schema_version", True),
    ("degree", "polish"), ("model_calls", True), ("model_calls", 1), ("model_calls", 4),
    ("model", None), ("diagnosis", None), ("text", "Changed."), ("extra", "private")])
def test_ctr01_rewrite_rejects_inconsistent_closed_response(key, value):
    payload, originals = fixture()
    payload[key] = value
    reject(payload, originals)


@pytest.mark.parametrize("code", [*MESSAGES, "request_budget"])
def test_ctr01_v2_errors_have_no_diagnoses_or_partial_content(code):
    payload = public_fixture(dict(tool="polish_text", provider=[], expect={"status": "error", "error": {"code": "provider_error"}}))
    payload.update(schema_version=2, degree="rewrite")
    payload["error"].update(code=code, message=BUDGET_MESSAGE if code == "request_budget" else MESSAGES[code])
    validate_rewrite_final(payload)
    for name, value in (("diagnosis", dict(status="no_issue", expression=None, reason=None)), ("text", "private"), ("items", [])):
        reject({**payload, name: value})
    payload["regeneration_attempted"] = True
    reject(payload)


@pytest.mark.parametrize("change", ["id", "order", "flag", "null", "substring", "usage", "count"])
def test_inv9_final_integrity_precedes_diagnostic_and_body_limits(change):
    payload, originals = fixture(2, "x" * 8000)
    a, b = payload["items"]
    a["diagnosis"] = dict(status="issue", expression="x", reason="r" * 321)
    if change == "id": b["id"] = "0"
    elif change == "order": payload["items"].reverse()
    elif change == "flag": b.update(text="changed", flag=dict(kind="unfixable", reason="Cannot edit.", checks=[]))
    elif change == "null": b["diagnosis"]["reason"] = "not null"
    elif change == "substring": b["diagnosis"] = dict(status="issue", expression="z", reason="Problem")
    elif change == "usage": payload.update(cost={"amount": "1.000000", "currency": "USD"})
    else: payload["model_calls"] = 17
    reject(payload, originals)


@pytest.mark.parametrize("length,code", [(16000, None), (16001, "output_limit")])
def test_ctr01_rewrite_body_limit_remains_inclusive(length, code):
    payload, originals = fixture(text="x" * length)
    if code: reject(payload, originals, code)
    else: validate_rewrite_final(payload, originals)


def test_ctr01_final_diagnostic_caps_and_retry_accounting():
    payload, originals = fixture(32, "x" * 160)
    payload["model_calls"] = 17
    for item in payload["items"]:
        item.update(regenerated=True, diagnosis=dict(status="issue", expression="x" * 160, reason="r" * 96))
    validate_rewrite_final(payload, originals)
    payload["items"][-1]["diagnosis"]["reason"] += "r"
    reject(payload, originals, "output_limit")
    payload["items"][-1]["diagnosis"]["reason"] = "r" * 321
    reject(payload, originals, "output_limit")


@pytest.mark.parametrize("extra", [0, 1])
def test_ctr01_complete_payload_byte_cap_includes_diagnoses(extra):
    payload, originals = fixture()
    payload["model"] = ""
    baseline = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    payload["model"] = "m" * (1048576 - baseline + extra)
    if extra: reject(payload, originals, "output_limit")
    else: validate_rewrite_final(payload, originals)


def test_inv9_candidate_no_issue_check_precedes_caps_and_flag_restoration():
    raw = json.dumps({"items": [{"id": "text", "text": "x" * 16001, "flag": {"kind": "unfixable", "reason": "Cannot edit."}}]})
    result = GenerationResult(raw, "stop", Usage(None, None, None))
    diagnoses = (DiagnosticItem("text", Diagnosis("no_issue", None, None)),)
    with pytest.raises(ValidationError) as caught:
        parse_generation(result, (SourceItem("text", "original", ""),), diagnoses)
    assert caught.value.code == "invalid_response"


@pytest.mark.parametrize("calls", [0, 1, 2, 3, 17, 18])
@pytest.mark.parametrize("retried", [False, True])
def test_ctr01_error_usage_counts_started_generations_without_inferred_retry(calls, retried):
    payload = public_fixture(dict(tool="polish_text", provider=[], expect={"status": "error", "error": {"code": "provider_error"}}))
    payload.update(schema_version=2, degree="rewrite", model_calls=calls, model_called=calls > 0,
                   regeneration_attempted=retried)
    payload["usage"] = dict.fromkeys(payload["usage"], None if calls else 0)
    if calls > 17 or retried and calls < 3:
        reject(payload)
    else:
        validate_rewrite_final(payload)
        payload["model_called"] = not payload["model_called"]
        reject(payload)
