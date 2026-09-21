"""Generation-4 complete shapes and correlations, with synthetic registry only."""
from copy import deepcopy
from functools import partial

import pytest
from jsonschema import Draft202012Validator

from copyeditor.edit_protocol import output_schema, validate_final
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
    validate_final(value, ORIGINALS, expected_enabled=value.get("judgment_enabled", False), registry=REGISTRY, **kwargs)


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


@pytest.mark.parametrize("key,value", [("policy_version", "unknown"), ("thresholds_version", "unknown"),
    ("policy_hash", "0" * 64), ("thresholds_hash", "0" * 64), ("policy_version", None),
    ("schema_version", True), ("schema_version", 3), ("diagnosis", "not null"), ("regenerated", True)])
def test_unknown_versions_mixed_nulls_and_inconsistent_fields_are_rejected(key, value):
    payload = fixture(True)
    payload[key] = value
    with pytest.raises(ValidationError):
        check(payload)


@pytest.mark.parametrize("change", ["missing_row", "extra_row", "wrong_provider", "nonzero_unused", "unknown_cost", "mode"])
def test_provider_rows_and_mode_are_correlated(change):
    value = fixture(True)
    if change == "missing_row": value["providers"].pop()
    if change == "extra_row": value["providers"].append(deepcopy(value["providers"][0]))
    if change == "wrong_provider": value["providers"][0]["provider"] = "typesafe"
    if change == "nonzero_unused": value["providers"][0]["model_calls"] = 0
    if change == "unknown_cost": value["cost"] = {"amount": "0.000001", "currency": "USD"}
    if change == "mode": value["judgment_enabled"] = False
    with pytest.raises(ValidationError):
        check(value)


@pytest.mark.parametrize("editing,judgment,estimation", [(0, 0, 0), (0, 1, 0), (0, 1, 1), (1, 1, 1), (0, 0, 1)])
def test_error_model_called_counts_generations_not_estimation(editing, judgment, estimation):
    value = fixture(True, route="error")
    for row, count in zip(value["providers"], (editing, judgment)):
        row.update(model_calls=count, latency_ms=int(bool(count)))
        if not count: row["usage"] = dict.fromkeys(row["usage"], 0)
    value["providers"][0]["estimation_calls"] = estimation
    value["model_called"] = bool(editing or judgment)
    check(value)
    value["model_called"] = not value["model_called"]
    with pytest.raises(ValidationError): check(value)


def test_lint_uses_tool_identity_and_has_no_editing_fields():
    value = fixture()
    value = {key: val for key, val in value.items() if key in output_schema("lint_text")["oneOf"][0]["properties"]}
    value.update(model=None, usage=dict(input_tokens=0, output_tokens=0, total_tokens=0), model_calls=0,
                 preservation=None, cost=None)
    validate_final(value, tool="lint_text")
    with pytest.raises(ValidationError): check(value)
    value["judgment_enabled"] = False
    with pytest.raises(ValidationError): validate_final(value, tool="lint_text")


def test_insufficient_is_unchanged_and_has_no_editing_calls_or_diagnosis():
    value = fixture(True, "rewrite")
    value.update(text="Original.", diagnosis=None, detection=dict(status="insufficient", reason="evaluated", probability=0.4))
    value["providers"][1]["model_calls"] = 1
    value["providers"][0].update(model_calls=0, estimation_calls=0, latency_ms=0,
                                 usage=dict(input_tokens=0, output_tokens=0, total_tokens=0))
    check(value)
    value["text"] = "Candidate."
    with pytest.raises(ValidationError): check(value)


@pytest.mark.parametrize("enabled", [False, True])
def test_input_error_has_null_language_and_zero_provider_counters(enabled):
    value = fixture(enabled, route="error")
    value.update(language=None, degree=None, model_called=False,
                 error=dict(code="invalid_input", message=MESSAGES["invalid_input"], field="degree"))
    for row in value["providers"]:
        row.update(model_calls=0, estimation_calls=0, latency_ms=0, usage=dict.fromkeys(row["usage"], 0))
    check(value)
    value["providers"][0]["estimation_calls"] = 1
    with pytest.raises(ValidationError): check(value)


@pytest.mark.parametrize("diagnosis,code", [("x\ny", "invalid_response"), (" ", "invalid_response"), ("x" * 321, "output_limit")])
def test_rewrite_line_integrity_and_valid_shape_size_are_distinct(diagnosis, code):
    value = fixture(False, "rewrite")
    value["diagnosis"] = diagnosis
    with pytest.raises(ValidationError) as error: check(value)
    assert error.value.code == code


@pytest.mark.parametrize("route", ["text", "error"])
@pytest.mark.parametrize("field", ["model_calls", "estimation_calls", "latency_ms"])
def test_provider_counters_reject_integral_floats(route, field):
    value = fixture(route=route)
    value["providers"][0][field] = 1.0
    with pytest.raises(ValidationError): check(value)


def test_changed_candidate_requires_a_verification_call_but_unfixable_does_not():
    value = fixture(True)
    value["providers"][1]["model_calls"] = 1
    with pytest.raises(ValidationError): check(value)
    value.update(text="Original.", flag=dict(kind="unfixable", reason="Ambiguous.", checks=[]))
    check(value)


@pytest.mark.parametrize("enabled,total,accepted", [(False, "0.000001", True), (False, "999.000000", False),
    (True, "0.000001", True), (True, "0.000002", True), (True, "0.000004", False)])
def test_cost_consistency_preserves_round_once_totals(enabled, total, accepted):
    value = fixture(enabled)
    for row in value["providers"]:
        row.update(usage=dict(input_tokens=1, output_tokens=1, total_tokens=2),
                   cost=dict(amount="0.000001", currency="USD"))
    value["cost"] = dict(amount=total, currency="USD")
    if accepted:
        check(value)
    else:
        with pytest.raises(ValidationError): check(value)


@pytest.mark.parametrize("field,value", [("text", "\ud800"), ("latency_ms", float("nan")),
    ("schema_version", 4.0), ("schema_version", True), ("policy_version", "unexpected")])
def test_shape_rejects_invalid_unicode_nonfinite_numbers_and_mixed_nulls(field, value):
    payload = fixture()
    payload[field] = value
    with pytest.raises(ValidationError): check(payload)


@pytest.mark.parametrize("amount,total,accepted", [("0.000000", "0.000001", True),
    ("0.000000", "0.000002", False)])
def test_cost_allows_rounding_difference_in_both_directions(amount, total, accepted):
    value = fixture(True)
    for row in value["providers"]:
        row.update(usage=dict(input_tokens=1, output_tokens=1, total_tokens=2), cost=dict(amount=amount, currency="USD"))
    value["cost"] = dict(amount=total, currency="USD")
    if accepted:
        check(value)
    else:
        with pytest.raises(ValidationError): check(value)


@pytest.mark.parametrize("total", ["0.100000", "0.100001"])
def test_enabled_one_called_provider_requires_exact_total(total):
    value = fixture(True, route="error")
    value["providers"][0].update(model_calls=0, estimation_calls=0, latency_ms=0,
                                usage=dict(input_tokens=0, output_tokens=0, total_tokens=0))
    value["providers"][1].update(usage=dict(input_tokens=1, output_tokens=1, total_tokens=2),
                                cost=dict(amount="0.100000", currency="USD"))
    value["cost"] = dict(amount=total, currency="USD")
    if total == "0.100000":
        check(value)
    else:
        with pytest.raises(ValidationError): check(value)
