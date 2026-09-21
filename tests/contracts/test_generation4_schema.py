"""Generation-4 complete shapes and correlations, with synthetic registry only."""
from copy import deepcopy
from functools import partial

import pytest
from jsonschema import Draft202012Validator

from copyeditor.edit_protocol import output_schema, validate_shape
from copyeditor.judgment_v2 import POLICY_ID, snapshot
from copyeditor.providers.base import SourceItem
from copyeditor.requests import MESSAGES, ValidationError
from .test_judged_schema import at, objects

REGISTRY = partial(snapshot, thresholds={"synthetic": {"id": "synthetic", "floor": 0.5, "gap": 0.2,
                                                     "meaning_floor": 0.8}}, pairs={(POLICY_ID, "synthetic")})
SELECTED = REGISTRY(POLICY_ID, "synthetic")
ORIGINALS = (SourceItem("a", "Original.", ""),)


def fixture(enabled=False, degree="polish", route="text"):
    rows = [dict(role=role, provider=provider, model=model, model_calls=1 if role == "editing" else 2, estimation_calls=int(role == "editing"),
                 usage=dict(input_tokens=None, output_tokens=None, total_tokens=None), cost=None, latency_ms=1)
            for role, provider, model in (("editing", "vertex", "editor"), ("judgment", "typesafe", "jev-1.13.0"))][:2 if enabled else 1]
    value = dict(schema_version=4, judgment_enabled=enabled, degree=degree, language="en",
        rules_version="sha256:" + "a" * 64, common_version="sha256:" + "b" * 64, providers=rows, cost=None, latency_ms=2,
        policy_version=POLICY_ID if enabled else None, thresholds_version="synthetic" if enabled else None,
        policy_hash=SELECTED.policy_hash if enabled else None, thresholds_hash=SELECTED.threshold_hash if enabled else None)
    if route == "error":
        return dict(value, status="error", error=dict(code="provider_error", message=MESSAGES["provider_error"], field=None),
                    model_called=True, regeneration_attempted=False)
    item = dict(text="Candidate.", flag=None, regenerated=False, protected_terms=[], findings=[], findings_truncated=False,
        diagnosis="Simplified wording." if degree == "rewrite" else None,
        detection=dict(status="eligible", reason="evaluated", probability=0.8) if enabled else None)
    value.update(status="ok", protected_terms_checked=0, preservation=dict(length_ratio=dict(min=0.5, max=2)))
    return dict(value, items=[dict(id="a", **item)]) if route == "items" else dict(value, **item)


def check(value, **kwargs):
    validate_shape(value, **kwargs)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("degree", ["polish", "rewrite"])
@pytest.mark.parametrize("route", ["text", "items", "error"])
def test_complete_closed_shape_requires_all_fields_in_both_modes(enabled, degree, route):
    value = fixture(enabled, degree, route)
    assert Draft202012Validator(output_schema("polish_text")).is_valid(value)
    check(value)
    for path, obj in objects(value):
        for key in obj:
            broken = deepcopy(value)
            del at(broken, path)[key]
            with pytest.raises(ValidationError):
                check(broken)
        broken = deepcopy(value)
        at(broken, path)["verification"] = 0.8
        with pytest.raises(ValidationError):
            check(broken)


def test_lint_uses_tool_identity_and_has_no_editing_fields():
    value = fixture()
    value = {key: val for key, val in value.items() if key in output_schema("lint_text")["oneOf"][0]["properties"]}
    value.update(model=None, usage=dict(input_tokens=0, output_tokens=0, total_tokens=0), model_calls=0,
                 preservation=None, cost=None)
    validate_shape(value, tool="lint_text")
    with pytest.raises(ValidationError): check(value)
    value["judgment_enabled"] = False
    with pytest.raises(ValidationError): validate_shape(value, tool="lint_text")


@pytest.mark.parametrize("route", ["text", "error"])
@pytest.mark.parametrize("field", ["model_calls", "estimation_calls", "latency_ms"])
def test_provider_counters_reject_integral_floats(route, field):
    value = fixture(route=route)
    value["providers"][0][field] = 1.0
    with pytest.raises(ValidationError): check(value)


@pytest.mark.parametrize("field,value", [("text", "\ud800"), ("latency_ms", float("nan")),
    ("schema_version", 4.0), ("schema_version", True), ("policy_version", "unexpected")])
def test_shape_rejects_invalid_unicode_nonfinite_numbers_and_mixed_nulls(field, value):
    payload = fixture()
    payload[field] = value
    with pytest.raises(ValidationError): check(payload)
