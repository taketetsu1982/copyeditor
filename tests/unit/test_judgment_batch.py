"""Prepared-request observations; no provider quality claim.
AC-08-4: comparative verify questions; AC-08-8: allowed state and request isolation.
AC-08-12: deterministic bounded packing; AC-08-14: versioned questions/references.
AC-08-16: original ordinals and whole HTML in the state."""
import json

import pytest

from copyeditor.judgment import (
    ACTION_CRITERIA, ACTION_INSTRUCTIONS, JudgmentBlock, JudgmentInput, POLICY,
    REFERENCES, _json_value,
)
from copyeditor.judgment_batch import JudgmentBudgetError, prepare_judgments
from copyeditor.providers.base import Background
from copyeditor.requests import ValidationError


def request(phase="detect", count=1, **changes):
    blocks = tuple(JudgmentBlock(i, "", "", "candidate" if phase == "verify" else None,
                                "simplify_phrasing" if phase == "verify" else None)
                   for i in range(1, count + 1))
    return JudgmentInput(phase, "ja", "text", Background("", "", "", ""), "", blocks)._replace(**changes)


def pad(data, length):
    return data._replace(blocks=data.blocks[:-1] + (data.blocks[-1]._replace(source="x" * length),))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


@pytest.mark.parametrize("phase,count,questions", [("detect", 3, 21), ("verify", 3, 12)])
def test_all_targets_share_one_request_with_exact_question_shapes(phase, count, questions):
    prepared = prepare_judgments(request(phase, count))
    assert prepared.plan.version == "request-pack-v1"
    assert prepared.plan.phase == phase
    assert prepared.plan.batches[0].ordinals == (1, 2, 3)
    assert len(prepared.requests) == 1
    payload = json.loads(prepared.requests[0])
    assert set(payload) == {"model", "state", "questions"}
    assert payload["model"] == "jev-1.13.0"
    assert len(payload["questions"]) == questions
    state = payload["state"]
    assert set(state) == set(POLICY["state"][phase])
    assert state["background"] == dict.fromkeys(("audience", "purpose", "tone", "message"), "")
    if phase == "detect":
        assert state["references"] == _json_value(REFERENCES)
    for key, question in payload["questions"].items():
        block_id, predicate = key.split(".")
        template = next(q for q in POLICY["questions"][phase] if q["id"] == predicate)
        expected = [POLICY["prefix"], POLICY["formats"]["text"],
                    f"Assess only {'texts' if phase == 'detect' else 'pairs'}.{block_id}.",
                    template["instructions"]]
        if phase == "verify":
            expected += ["simplify_phrasing", ACTION_INSTRUCTIONS["simplify_phrasing"]]
        assert question["instructions"] == "\n".join(expected)
        assert question["type"] == ("choice" if predicate == "action" else "noul")
        assert set(question) == ({"type", "instructions", "criteria"} if predicate == "action"
                                 else {"type", "instructions"})
        if predicate == "action":
            assert question["criteria"] == ACTION_CRITERIA
    batch = prepared.plan.batches[0]
    assert prepared.requests[0] == canonical(payload)
    assert batch.input_units == len(canonical(payload)) + 4096
    assert batch.state_question_units == len(canonical(state)) + max(
        len(canonical(q)) for q in payload["questions"].values()) + 4096


def test_input_unit_limit_accepts_equality_and_splits_one_byte_above():
    data = request(count=8)
    gap = 64000 - prepare_judgments(data).plan.batches[0].input_units
    exact = pad(data, gap)
    assert prepare_judgments(exact).plan.batches[0].input_units == 64000
    above = prepare_judgments(pad(data, gap + 1))
    assert tuple(b.ordinals for b in above.plan.batches) == (tuple(range(1, 8)), (8,))
    assert above == prepare_judgments(pad(data, gap + 1))
    for payload in above.requests:
        assert json.loads(payload)["state"]["references"] == _json_value(REFERENCES)


@pytest.mark.parametrize("phase", ["detect", "verify"])
def test_state_question_limit_accepts_equality_and_rejects_oversize_single_target(phase):
    data = request(phase)
    gap = 32000 - prepare_judgments(data).plan.batches[0].state_question_units
    assert prepare_judgments(pad(data, gap)).plan.batches[0].state_question_units == 32000
    with pytest.raises(JudgmentBudgetError) as error:
        prepare_judgments(pad(data, gap + 1))
    assert error.value.code == "request_budget"


@pytest.mark.parametrize("phase", ["detect", "verify"])
def test_state_limit_splits_only_between_complete_targets(phase):
    data = request(phase, 2)
    data = data._replace(blocks=tuple(b._replace(source="a" * 14000) for b in data.blocks))
    prepared = prepare_judgments(data)
    assert tuple(b.ordinals for b in prepared.plan.batches) == ((1,), (2,))
    for payload in prepared.requests:
        targets = json.loads(payload)["state"]["texts" if phase == "detect" else "pairs"]
        assert len(targets) == 1
        target = next(iter(targets.values()))
        assert target["text" if phase == "detect" else "original"] == "a" * 14000
        if phase == "verify":
            assert target["candidate"] == "candidate"


def test_phase_allowances_are_checked_for_the_whole_plan():
    data = request(count=20)
    prepared = prepare_judgments(data)
    calls = len(prepared.requests)
    units = sum(b.input_units for b in prepared.plan.batches)
    assert calls > 1
    assert prepare_judgments(data, remaining_calls=calls, remaining_input_units=units) == prepared
    for limits in ({"remaining_calls": calls - 1}, {"remaining_input_units": units - 1}):
        with pytest.raises(JudgmentBudgetError) as error:
            prepare_judgments(data, **limits)
        assert error.value.code == "request_budget"
    with pytest.raises(JudgmentBudgetError) as error:
        prepare_judgments(request(count=100))
    assert error.value.code == "request_budget"


@pytest.mark.parametrize("phase", ["detect", "verify"])
def test_empty_phase_needs_no_requests_or_allowance(phase):
    result = prepare_judgments(request(phase, 0), remaining_calls=0, remaining_input_units=0)
    assert result.requests == result.plan.batches == ()


def test_original_ordinals_raw_html_context_and_request_isolation():
    html = '<html><p>本文</p><script>protected()</script></html>'
    data = request("verify", format="html", background=Background("readers", "purpose", "tone", "message"),
                   desired_style="tone", blocks=(JudgmentBlock(12, html, "sibling", html, "trim_explanation"),))
    prepared = prepare_judgments(data)
    assert prepared.plan.batches[0].ordinals == (12,)
    payload = json.loads(prepared.requests[0])
    assert payload["state"]["pairs"] == {"b0012": {
        "original": html, "candidate": html, "context": "sibling", "action": "trim_explanation"}}
    assert payload["state"]["desired_style"] == "tone"
    for question in payload["questions"].values():
        assert html not in question["instructions"]
        assert POLICY["formats"]["html"] in question["instructions"]
        assert question["instructions"].endswith("trim_explanation\n" + ACTION_INSTRUCTIONS["trim_explanation"])
    assert b"sibling" not in prepare_judgments(request()).requests[0]
    assert prepared == prepare_judgments(data)


@pytest.mark.parametrize("changes", [{"policy_id": "unknown"}, {"remaining_calls": 65},
                                      {"remaining_input_units": 262145}])
def test_unknown_policy_and_expanded_allowances_are_rejected(changes):
    with pytest.raises(ValidationError):
        prepare_judgments(request(), **changes)
