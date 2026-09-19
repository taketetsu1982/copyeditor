import hashlib
import json
import re
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import get_args

import pytest

from copyeditor import judgment as j
from copyeditor.providers.base import Background, Usage


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
