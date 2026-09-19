from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from copyeditor.judged_response import judged_output_schema, validate_judged_shape
from copyeditor.judgment import ACTION_CRITERIA, POLICY, SNAPSHOT
from copyeditor.requests import ValidationError
from copyeditor.responses import tool_output_schema
from copyeditor.rewrite_response import BUDGET_MESSAGE, rewrite_output_schema


def judged_fixture(route="text", degree="polish", evaluated=True):
    providers = [dict(role=role, provider=provider, model=model, model_calls=0, estimation_calls=0,
                      usage=dict(input_tokens=0, output_tokens=0, total_tokens=0), cost=None, latency_ms=0)
                 for role, provider, model in (("editing", "vertex", "editor"), ("judgment", "typesafe", "jev-1.13.0"))]
    value = dict(schema_version=3, degree=degree, language="ja", rules_version="sha256:" + "a" * 64,
                 common_version="sha256:" + "b" * 64, providers=providers, cost=None, latency_ms=0,
                 policy_version=SNAPSHOT.policy_id, policy_hash=SNAPSHOT.policy_hash,
                 thresholds_version=SNAPSHOT.threshold_id, thresholds_hash=SNAPSHOT.threshold_hash)
    if route == "error":
        return dict(value, status="error", error=dict(code="request_budget", message=BUDGET_MESSAGE, field=None),
                    model_called=False, regeneration_attempted=False)
    detection = dict(status="not_run", reason="no_editable_prose", gate=None, checks=[], action=None)
    verification = dict(status="not_run", reason="not_generated", checks=[])
    if evaluated:
        action = "simplify_phrasing"
        detection = dict(status="eligible", reason="evaluated", gate=dict(probability=0.9, result="present"),
                         checks=[dict(id=axis, probability=0.8) for axis in POLICY["axis_order"]],
                         action=dict(selected=action, effective=action, source="choice", confidence=1,
                                     probabilities={key: int(key == action) for key in ACTION_CRITERIA}))
        verification = dict(status="pass", reason="evaluated", checks=[
            dict(id=key, probability=0.9, result="pass") for key in POLICY["verification"]["order"]])
    item = dict(text="Example.", flag=None, regenerated=False, protected_terms=[], findings=[], findings_truncated=False,
                diagnosis=dict(status="issue", expression="Example", reason="Simplify phrasing.")
                if degree == "rewrite" and evaluated else None,
                editing="generated" if evaluated else "not_run", detection=detection, verification=verification)
    value.update(status="ok", protected_terms_checked=0, preservation=dict(length_ratio=dict(min=0.5, max=2)))
    return dict(value, items=[dict(id="a", **item)]) if route == "items" else dict(value, **item)


def at(value, path):
    for key in path:
        value = value[key]
    return value


def objects(value, path=()):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from objects(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from objects(child, path + (index,))


@pytest.mark.parametrize("route", ["text", "items", "error"])
@pytest.mark.parametrize("degree", ["polish", "rewrite", None])
def test_complete_variants_require_every_field_and_close_every_object(route, degree):
    value = judged_fixture(route, degree)
    if degree is None and route != "error":
        with pytest.raises(ValidationError):
            validate_judged_shape(value)
        return
    validate_judged_shape(value)
    for path, obj in objects(value):
        for key in obj:
            broken = deepcopy(value)
            del at(broken, path)[key]
            with pytest.raises(ValidationError):
                validate_judged_shape(broken)
        broken = deepcopy(value)
        at(broken, path)["unknown"] = "PRIVATE"
        with pytest.raises(ValidationError):
            validate_judged_shape(broken)


@pytest.mark.parametrize("path,bad", [
    (("schema_version",), True), (("latency_ms",), 1.0), (("providers", 0, "model_calls"), True),
    (("providers", 1, "estimation_calls"), 1), (("providers", 0, "model"), "bad/name"),
    (("detection", "gate", "probability"), float("nan")), (("detection", "gate", "probability"), float("inf")),
    (("detection", "gate", "probability"), True), (("detection", "gate", "probability"), -0.01),
    (("detection", "action", "confidence"), 1.01), (("detection", "action", "selected"), "unknown"),
    (("detection", "checks", 0, "id"), "unknown"), (("verification", "checks", 0, "id"), "scope"),
    (("policy_hash",), "A" * 64), (("policy_version",), "unknown"), (("text",), "\ud800"),
    (("providers", 0, "usage", "total_tokens"), False), (("regenerated",), 0),
    (("detection", "action"), None), (("detection", "gate"), None), (("verification", "checks"), []),
])
def test_wrong_types_values_and_nulls_are_rejected(path, bad):
    value = judged_fixture()
    at(value, path[:-1])[path[-1]] = bad
    with pytest.raises(ValidationError) as caught:
        validate_judged_shape(value)
    assert caught.value.code == "invalid_response" and caught.value.field is None


def test_not_run_nulls_and_all_seven_action_keys():
    value = judged_fixture(evaluated=False)
    validate_judged_shape(value)
    for key in ("gate", "action"):
        broken = deepcopy(value)
        broken["detection"][key] = judged_fixture()["detection"][key]
        with pytest.raises(ValidationError):
            validate_judged_shape(broken)
    for action in ACTION_CRITERIA:
        value = judged_fixture()
        value["detection"]["action"].update(selected=action, effective=action)
        validate_judged_shape(value)
        del value["detection"]["action"]["probabilities"][action]
        with pytest.raises(ValidationError):
            validate_judged_shape(value)


def test_nullable_usage_cost_diagnosis_and_verification_flags():
    value = judged_fixture(degree="rewrite")
    value["diagnosis"] = dict(status="no_issue", expression=None, reason=None)
    for row in value["providers"]:
        row["usage"] = dict.fromkeys(row["usage"])
        row["cost"] = dict(amount="0.000001", currency="USD")
    value["cost"] = dict(amount="0.000002", currency="USD")
    for reason in ("Candidate verification failed.", "Candidate verification was inconclusive."):
        value["flag"] = dict(kind="verification_rejected", reason=reason, checks=["meaning", "achieved"])
        validate_judged_shape(value)
    value["flag"]["checks"] = ["meaning", "meaning"]
    with pytest.raises(ValidationError):
        validate_judged_shape(value)


def test_schema_is_fresh_and_legacy_discovery_is_unchanged():
    before = deepcopy(tool_output_schema("polish_text"))
    schema = judged_output_schema()
    Draft202012Validator.check_schema(schema)
    schema["oneOf"][0]["properties"].clear()
    validate_judged_shape(judged_fixture())
    assert tool_output_schema("polish_text") == before
    assert not Draft202012Validator(before).is_valid(judged_fixture())
    assert rewrite_output_schema()["oneOf"][0]["properties"]["schema_version"]["const"] == 2


def test_inherited_findings_flags_and_limits_remain_closed():
    value = judged_fixture()
    value["findings"] = [dict(rule_id="ja-vocabulary-001", start=0, end=7, matched="Example",
                              matched_truncated=False, message="Use familiar wording.")]
    value["flag"] = dict(kind="unfixable", reason="Cannot safely edit.", checks=[])
    validate_judged_shape(value)
    for path, obj in objects(value["findings"]):
        for key in obj:
            broken = deepcopy(value)
            del at(broken["findings"], path)[key]
            with pytest.raises(ValidationError):
                validate_judged_shape(broken)
    value["flag"] = dict(kind="rejected", reason="Preservation checks failed.", checks=["numbers"])
    validate_judged_shape(value)
    value["text"] = "x" * 16001
    with pytest.raises(ValidationError):
        validate_judged_shape(value)
    validate_judged_shape(value, limits=False)
    value = judged_fixture("items")
    for providers in (value["providers"][:1], value["providers"][::-1], value["providers"] * 2):
        with pytest.raises(ValidationError):
            validate_judged_shape(dict(value, providers=providers))
