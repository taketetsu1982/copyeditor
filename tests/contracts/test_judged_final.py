from copy import deepcopy

import pytest

from copyeditor.judged_response import validate_judged_final
from copyeditor.providers.base import SourceItem
from copyeditor.requests import ValidationError
from .test_judged_schema import judged_fixture, at


ORIGINALS = (SourceItem("a", "Example.", ""),)


def fixture(route="text", degree="polish"):
    value = judged_fixture(route, degree)
    for row, calls in zip(value["providers"], (2 if degree == "rewrite" else 1, 2)):
        row["model_calls"] = calls
        row["usage"] = dict.fromkeys(row["usage"])
    return value


def reject(value, originals=ORIGINALS, code="invalid_response", **kwargs):
    with pytest.raises(ValidationError) as caught:
        validate_judged_final(value, originals, **kwargs)
    assert caught.value.code == code


@pytest.mark.parametrize("route", ["text", "items"])
@pytest.mark.parametrize("degree", ["polish", "rewrite"])
def test_complete_generated_results_and_input_identity(route, degree):
    value = fixture(route, degree)
    validate_judged_final(value, ORIGINALS)
    reject(value, ())
    reject(value, ORIGINALS * 2)
    if route == "items":
        reject(value, (SourceItem("other", "Example.", ""),))


@pytest.mark.parametrize("editing,judgment,estimation,expected", [
    (0, 0, 0, False), (0, 1, 0, True), (0, 1, 1, True), (1, 1, 1, True), (0, 0, 1, False)])
def test_error_model_called_observes_both_rows_not_estimation(editing, judgment, estimation, expected):
    value = judged_fixture("error")
    for row, count in zip(value["providers"], (editing, judgment)):
        row["model_calls"] = count
        if count:
            row["usage"] = dict.fromkeys(row["usage"])
    value["providers"][0]["estimation_calls"] = estimation
    value["model_called"] = expected
    validate_judged_final(value)
    value["model_called"] = not expected
    reject(value)


@pytest.mark.parametrize("path,bad", [(("policy_hash",), "0" * 64), (("thresholds_hash",), "0" * 64),
    (("detection", "status"), "insufficient"), (("detection", "gate", "result"), "absent"),
    (("detection", "action", "source"), "axis_fallback"), (("detection", "action", "effective"), "trim_explanation"),
    (("detection", "action", "probabilities", "preserve_as_is"), 0.1),
    (("detection", "action", "selected"), "preserve_as_is"), (("verification", "status"), "fail"),
    (("verification", "checks", 0, "result"), "indeterminate"), (("editing",), "not_run"),
    (("diagnosis",), dict(status="no_issue", expression=None, reason=None)), (("regenerated",), True),
    (("providers", 0, "model_calls"), 0), (("providers", 1, "model_calls"), 0),
    (("protected_terms_checked",), 1)])
def test_shape_valid_but_inconsistent_results_fail(path, bad):
    value = fixture()
    at(value, path[:-1])[path[-1]] = bad
    reject(value)


@pytest.mark.parametrize("gate,confidence", [(0.53, 0), (0.53, 1), (0.529999, 0)])
def test_gate_boundary_raw_preserve_and_tied_axis_fallback(gate, confidence):
    value = fixture()
    detection = value["detection"]
    detection["gate"]["probability"] = gate
    action = detection["action"]
    action.update(selected="preserve_as_is", confidence=confidence, effective="simplify_vocabulary", source="axis_fallback")
    action["probabilities"] = {key: int(key == "preserve_as_is") for key in action["probabilities"]}
    if gate < 0.53:
        detection.update(status="insufficient", gate=dict(probability=gate, result="absent"))
        action.update(effective="preserve_as_is", source="choice")
        value.update(editing="not_run", verification=dict(status="not_run", reason="not_generated", checks=[]))
        value["providers"][0].update(model_calls=0, usage=dict(input_tokens=0, output_tokens=0, total_tokens=0))
        value["providers"][1]["model_calls"] = 1
    validate_judged_final(value, ORIGINALS)
    value["text"] = "Changed." if gate < 0.53 else value["text"]
    action["effective"] = "trim_explanation"
    reject(value)


@pytest.mark.parametrize("probability,status,reason", [(0.2, "fail", "Candidate verification failed."),
    (0.5, "indeterminate", "Candidate verification was inconclusive.")])
def test_verification_rejection_restores_original_and_lists_every_nonpass(probability, status, reason):
    value = fixture()
    value["verification"]["checks"][0].update(probability=probability, result=status)
    value["verification"]["status"] = status
    value["flag"] = dict(kind="verification_rejected", reason=reason, checks=["meaning"])
    validate_judged_final(value, ORIGINALS)
    broken = deepcopy(value)
    broken["flag"]["checks"] = ["scope"]
    reject(broken)
    value["text"] = "Changed."
    reject(value)


def test_diagnosed_no_issue_and_html_not_run_are_restricted():
    value = fixture(degree="rewrite")
    value.update(diagnosis=dict(status="no_issue", expression=None, reason=None), editing="diagnosed_no_issue",
                 verification=dict(status="not_run", reason="not_generated", checks=[]))
    for row in value["providers"]:
        row["model_calls"] = 1
    validate_judged_final(value, ORIGINALS)
    value["text"] = "Changed."
    reject(value)
    value = judged_fixture(evaluated=False)
    value["text"] = "<code>protected</code>"
    originals = (SourceItem("a", value["text"], ""),)
    validate_judged_final(value, originals, format="html")
    reject(value, originals)
    reject(value, ORIGINALS, format="html")


def test_diagnostic_and_body_caps_and_flag_priority():
    value = fixture(degree="rewrite")
    value["diagnosis"]["expression"] = "absent"
    reject(value)
    value["diagnosis"]["expression"] = "Example"
    value["diagnosis"]["reason"] = "x" * 321
    reject(value, code="output_limit")
    value = fixture()
    value["text"] = "x" * 16001
    reject(value, code="output_limit")
    value = fixture()
    value.update(flag=dict(kind="unfixable", reason="Cannot edit.", checks=[]),
                 verification=dict(status="not_run", reason="unfixable", checks=[]))
    value["providers"][1]["model_calls"] = 1
    validate_judged_final(value, ORIGINALS)
    value["verification"] = fixture()["verification"]
    reject(value)


def test_item_order_duplicates_and_aggregate_body_limit():
    value = fixture("items")
    value["items"].append(dict(deepcopy(value["items"][0]), id="b"))
    originals = ORIGINALS + (SourceItem("b", "Example.", ""),)
    validate_judged_final(value, originals)
    reject(value, originals[::-1])
    broken = deepcopy(value)
    broken["items"][1]["id"] = "a"
    reject(broken, originals)
    for item in value["items"]:
        item["text"] = "x" * 8000
    validate_judged_final(value, originals)
    value["items"][1]["text"] += "x"
    reject(value, originals, code="output_limit")
