import hashlib
import json
import math
import re
from itertools import product
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import get_args

import pytest

from copyeditor import judgment as j
from copyeditor.providers.base import Background, Usage
from copyeditor.requests import ValidationError


def test_questions_cover_gate_axes_actions_and_comparative_verification():
    detect, verify = j.QUESTIONS["detect"], j.QUESTIONS["verify"]
    assert tuple(q["id"] for q in detect) == ("gate", "stiff", "abstract", "formulaic", "roundabout", "repetitive", "action")
    assert tuple(q["id"] for q in verify) == ("meaning", "scope", "natural", "achieved")
    assert all(q["type"] == "noul" and "criteria" not in q for q in (*detect[:-1], *verify))
    assert detect[-1]["type"] == "choice" and detect[-1]["criteria"] == j.ACTION_CRITERIA
    for problem in ("stiff", "abstract", "generic", "formulaic", "roundabout", "mechanically repetitive"):
        assert problem in detect[0]["instructions"]
    assert "generic or formulaic" in detect[3]["instructions"]
    assert "redundant explanation" in detect[4]["instructions"]
    assert "more natural than the original" in verify[2]["instructions"]
    assert tuple(j.POLICY["fallback"]) == j.POLICY["axis_order"]
    assert tuple(j.POLICY["fallback"].values()) == (
        "simplify_vocabulary", "make_more_concrete", "make_more_specific", "simplify_phrasing", "simplify_structure")


def test_actions_match_public_contract_and_each_has_one_instruction():
    contract = (Path(__file__).resolve().parents[2] / "contracts/tools.md").read_text()
    table = contract.split("| Action | Bounded editing intent |\n")[1].split("\n\n")[0]
    assert dict(re.findall(r"^\| ([a-z_]+) \| (.*?) \|$", table, re.M)) == j.ACTION_CRITERIA
    assert set(get_args(j.Action)) == set(j.ACTION_CRITERIA) == set(j.ACTION_INSTRUCTIONS)
    assert len(j.ACTION_INSTRUCTIONS) == 7
    assert all(isinstance(value, str) and value.endswith("。") and "\n" not in value
               for value in j.ACTION_INSTRUCTIONS.values())
    assert [(ref["id"], ref["label"]) for ref in j.REFERENCES] == [
        (f"ref-{label}-{n}", label) for label in ("natural", "unnatural") for n in range(1, 4)]


def test_versioned_definitions_have_stable_canonical_hashes():
    assert set(j.POLICIES) == {"reference-gate-action-v1"}
    assert set(j.THRESHOLDS) == {"gate-floor-v1"}
    assert j.COMPATIBLE_PAIRS == {(j.POLICY_ID, j.THRESHOLD_ID)}
    assert j.SNAPSHOT.policy_hash == "6018bdd5e97c0c9792e61820f675ae2b63a17cb5393e0756605ac48556893c54"
    assert j.SNAPSHOT.threshold_hash == "fab230b94aba689ee1d1938bc3d1b4461ed0684229f3941c2c71823783bdcc07"
    expected = hashlib.sha256('{"a":"日本語","z":[1,0.53]}'.encode()).hexdigest()
    assert j.definition_hash({"z": (1, 0.53), "a": "日本語"}) == expected
    assert j.THRESHOLD == {"id": "gate-floor-v1", "floor": 0.53}


@pytest.mark.parametrize("path", [
    ("id",), ("prefix",), ("formats", "html"), ("targets", "detect"),
    ("questions", "detect", 0, "instructions"), ("questions", "detect", 0, "type"),
    ("questions", "detect", 6, "criteria", "preserve_as_is"),
    ("references", 0, "text"), ("references", 0, "label"), ("references", 0, "id"),
    ("action_instructions", "trim_explanation"), ("state", "pairs"), ("packing_version",),
    ("verification", "pass_min"), ("fallback", "stiff"), ("axis_order",),
])
def test_policy_hash_covers_every_semantic_component(path):
    definition = json.loads(json.dumps(j.POLICY, default=lambda value: dict(value)))
    target = definition
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = "changed"
    assert j.definition_hash(definition) != j.SNAPSHOT.policy_hash
    assert j.definition_hash({"id": j.THRESHOLD_ID, "floor": 0.54}) != j.SNAPSHOT.threshold_hash


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_definition_hash_rejects_nonfinite_values(value):
    with pytest.raises(ValueError):
        j.definition_hash({"floor": value})


def test_reference_order_changes_policy_hash():
    definition = dict(j.POLICY)
    definition["references"] = tuple(reversed(j.REFERENCES))
    assert j.definition_hash(definition) != j.SNAPSHOT.policy_hash
    assert j.definition_hash(dict(reversed(tuple(j.POLICY.items())))) == j.SNAPSHOT.policy_hash


def test_definitions_and_dtos_are_immutable_and_require_nullable_fields():
    for mapping, key in ((j.POLICIES, j.POLICY_ID), (j.POLICY, "id"),
                         (j.REFERENCES[0], "text"), (j.QUESTIONS["detect"][0], "type")):
        with pytest.raises(TypeError):
            mapping[key] = "changed"
    probabilities = {action: float(action == "preserve_as_is") for action in j.ACTION_CRITERIA}
    choice = j.JudgmentChoice("preserve_as_is", probabilities, 0.0)
    probabilities["preserve_as_is"] = 0
    assert choice.probabilities["preserve_as_is"] == 1
    with pytest.raises(TypeError):
        choice.probabilities["preserve_as_is"] = 0
    with pytest.raises(FrozenInstanceError):
        choice.confidence = 1
    block = j.JudgmentBlock(1, "original", "context", None, None)
    request = j.JudgmentInput("detect", "ja", "text", Background("", "", "", ""), "", (block,))
    result = j.JudgmentResult("jev-1.13.0", (j.JudgmentBlockResult(1, (("gate", 0.1),), choice),), Usage(None, None, None))
    plan = j.BatchPlan("request-pack-v1", "detect", (j.JudgmentBatch((1,), 1, 1),))
    for value in (block, request, result.blocks[0], result, plan.batches[0], plan, j.SNAPSHOT,
                  j.JudgmentFailure("invalid_response", result.usage)):
        assert not value._field_defaults
        with pytest.raises(AttributeError):
            setattr(value, value._fields[0], None)
    with pytest.raises(TypeError):
        j.JudgmentBlock(1, "original", "context")


PUBLIC = (Path(__file__).resolve().parents[2] / "contracts/tools.md").read_text()
FLOOR = float(re.search(r"p >= ([\d.]+): present", PUBLIC)[1])
FAIL, PASS = map(float, re.search(r"Fixed p <= ([\d.]+): fail; p >= ([\d.]+): pass", PUBLIC).groups())
FALLBACK = dict(re.findall(r"([a-z_]+)→([a-z_]+)", PUBLIC))
AXES = tuple(re.search(r"\| Axes \| ([^|]+) \|", PUBLIC)[1].strip().split(", "))
VERIFY = tuple(re.search(r"\| Verification \| ([^|]+) \|", PUBLIC)[1].strip().split(", "))


def distribution(selected="preserve_as_is"):
    return {key: int(key == selected) for key in j.ACTION_CRITERIA}


def detection(gate, selected="preserve_as_is", axes=None, confidence=0):
    probabilities = (("gate", gate), *zip(AXES, axes if axes is not None else [0] * 5))
    return j.JudgmentBlockResult(1, probabilities, j.JudgmentChoice(selected, distribution(selected), confidence))


@pytest.mark.parametrize("gate,present", [(math.nextafter(FLOOR, 0), False), (FLOOR, True),
                                          (math.nextafter(FLOOR, 1), True), (0.5, False), (0, False), (1, True)])
@pytest.mark.parametrize("selected", ["preserve_as_is", "trim_explanation"])
def test_detection_matches_public_aggregation_rows(gate, present, selected):
    block = detection(gate, selected)
    result = j.classify_detection(block)
    fallback = present and selected == "preserve_as_is"
    assert result["status"] == ("eligible" if present else "insufficient")
    assert result["gate"] == {"probability": gate, "result": "present" if present else "absent"}
    assert result["action"]["source"] == ("axis_fallback" if fallback else "choice")
    assert result["action"]["effective"] == (FALLBACK[AXES[0]] if fallback else selected)
    assert result["action"]["selected"] == selected
    assert result["action"]["probabilities"] == distribution(selected)
    assert [check["id"] for check in result["checks"]] == list(AXES)


@pytest.mark.parametrize("axis", AXES)
def test_only_eligible_preserve_uses_max_axis_and_confidence_never_changes_it(axis):
    values = [int(key == axis) for key in AXES]
    for gate, selected in product((0, FLOOR), ("preserve_as_is", "trim_explanation")):
        outputs = [j.classify_detection(detection(gate, selected, values, confidence)) for confidence in (0, 0.17, 1)]
        assert [output["action"].pop("confidence") for output in outputs] == [0, 0.17, 1]
        assert outputs[0] == outputs[1] == outputs[2]
        assert outputs[0]["action"]["effective"] == (FALLBACK[axis] if gate == FLOOR and selected == "preserve_as_is" else selected)
    tied = [0.9] * 5
    assert j.classify_detection(detection(FLOOR, axes=tied))["action"]["effective"] == FALLBACK[AXES[0]]


@pytest.mark.parametrize("total", [0.999999, 1, 1.000001])
def test_choice_tolerance_preserves_values_and_tied_maxima(total):
    values = distribution()
    values.update(preserve_as_is=0.5, trim_explanation=round(total - 0.5, 6))
    selected = "trim_explanation" if total > 1 else "preserve_as_is"
    choice = j.validate_choice(selected, list(reversed(tuple(values.items()))), 0.17)
    assert dict(choice.probabilities) == values and choice.confidence == 0.17
    if total == 1:
        assert j.validate_choice("trim_explanation", values, 0).selected == "trim_explanation"


@pytest.mark.parametrize("bad", [None, True, False, "0.5", float("nan"), float("inf"), -0.01, 1.01])
def test_bad_numbers_are_rejected_even_when_gate_is_absent(bad):
    with pytest.raises(ValidationError) as error:
        j.classify_detection(detection(bad))
    assert error.value.code == "invalid_response"
    for field in ("axis", "probability", "confidence", "verification"):
        with pytest.raises(ValidationError) as error:
            if field == "axis":
                j.classify_detection(detection(0, axes=[bad, 0, 0, 0, 0]))
            elif field == "verification":
                j.classify_verification(j.JudgmentBlockResult(1, tuple(zip(VERIFY, [bad, 1, 1, 1])), None))
            else:
                values = distribution()
                values["preserve_as_is"] = bad if field == "probability" else 1
                j.classify_detection(detection(0)._replace(choice=j.JudgmentChoice(
                    "preserve_as_is", values, bad if field == "confidence" else 0)))
        assert error.value.code == "invalid_response"


@pytest.mark.parametrize("case", ["missing", "extra", "duplicate", "nonmax", "unknown", "low_sum", "high_sum"])
def test_choice_rejects_invalid_shape_selection_and_sum(case):
    values = distribution(); selected = "preserve_as_is"
    if case == "missing": del values["trim_explanation"]
    if case == "extra": values["unknown"] = 0
    if case == "duplicate": values = [*values.items(), ("preserve_as_is", 1)]
    if case == "nonmax": selected = "trim_explanation"
    if case == "unknown": selected = "unknown"
    if case == "low_sum": values["preserve_as_is"] = 0.999998
    if case == "high_sum": values["trim_explanation"] = 0.000002
    with pytest.raises(ValidationError) as error:
        j.validate_choice(selected, values, 0)
    assert error.value.code == "invalid_response"


@pytest.mark.parametrize("value,result", [(0, "fail"), (FAIL, "fail"), (math.nextafter(FAIL, 1), "indeterminate"),
                                         (math.nextafter(PASS, 0), "indeterminate"), (PASS, "pass"), (1, "pass")])
@pytest.mark.parametrize("index", range(4))
def test_each_verification_boundary_matches_public_contract(value, result, index):
    values = [1] * 4; values[index] = value
    output = j.classify_verification(j.JudgmentBlockResult(1, tuple(zip(VERIFY, values)), None))
    assert output["checks"][index]["result"] == output["status"] == result
    assert [check["id"] for check in output["checks"]] == list(VERIFY)


@pytest.mark.parametrize("results", list(product(("fail", "indeterminate", "pass"), repeat=4)))
def test_verification_aggregation_matches_public_all_pass_any_fail_rule(results):
    numbers = {"fail": FAIL, "indeterminate": (FAIL + PASS) / 2, "pass": PASS}
    output = j.classify_verification(j.JudgmentBlockResult(1, tuple((key, numbers[result]) for key, result in zip(VERIFY, results)), None))
    assert output["status"] == ("fail" if "fail" in results else "indeterminate" if "indeterminate" in results else "pass")


@pytest.mark.parametrize("classify,block", [(j.classify_detection, detection(0)),
    (j.classify_verification, j.JudgmentBlockResult(1, tuple(zip(VERIFY, [1] * 4)), None))])
def test_missing_extra_duplicate_checks_and_unknown_versions_are_rejected(classify, block):
    for values in (block.probabilities[:-1], (*block.probabilities, block.probabilities[0]),
                   (*block.probabilities, ("should_edit", 1)), None, (("gate",),)):
        with pytest.raises(ValidationError):
            classify(block._replace(probabilities=values))
    for versions in (("expression-v1", "conservative-v1"), (j.POLICY_ID, "unknown"), ([], j.THRESHOLD_ID)):
        with pytest.raises(ValidationError):
            classify(block, policy_id=versions[0], threshold_id=versions[1])


@pytest.mark.parametrize("second", [math.nextafter(0.499999, 0), math.nextafter(0.500001, 1)])
def test_choice_rejects_immediately_outside_sum_tolerance(second):
    values = distribution(); values.update(preserve_as_is=0.5, trim_explanation=second)
    with pytest.raises(ValidationError):
        j.validate_choice(max(values, key=values.get), values, 0)


def test_input_order_does_not_change_output_and_raw_choice_is_not_relabelled():
    block = detection(FLOOR, axes=[0, 0, 1, 0, 0])
    result = j.classify_detection(block._replace(probabilities=tuple(reversed(block.probabilities))))
    assert result == j.classify_detection(block)
    assert result["action"]["effective"] == "make_more_specific"
    assert result["action"]["probabilities"]["make_more_specific"] == 0
    result["action"]["probabilities"]["preserve_as_is"] = 0
    assert block.choice.probabilities["preserve_as_is"] == 1
    with pytest.raises(ValidationError):
        j.classify_detection(block._replace(choice=None))
    with pytest.raises(ValidationError):
        j.classify_verification(j.JudgmentBlockResult(1, tuple(zip(VERIFY, [1] * 4)), block.choice))
