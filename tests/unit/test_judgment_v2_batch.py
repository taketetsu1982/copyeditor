"""AC-08-8/12: observe actual v2 bytes, packing boundaries and phase admission."""
import json

import pytest

from copyeditor.judgment import JudgmentBlock, JudgmentInput, _json_value
from copyeditor.judgment_v2 import GATE, MEANING, PREFIX, REFERENCES
from copyeditor.judgment_v2_batch import JudgmentBudgetError, prepare_judgments
from copyeditor.providers.base import Background
from copyeditor.requests import ValidationError


def request(phase="detect", count=1, **changes):
    blocks = tuple(JudgmentBlock(i, "source", "context", "candidate", "forbidden-action")
                   for i in range(1, count + 1))
    return JudgmentInput(phase, "ja", "text", Background("", "", "forbidden-tone", ""),
                         "forbidden-style", blocks)._replace(**changes)


def prepare(data, **kwargs):
    kwargs.setdefault("candidate_round", 0 if data.phase == "detect" else 1)
    return prepare_judgments(data, **kwargs)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


@pytest.mark.parametrize("phase,round_", [("detect", 0), ("verify", 1), ("verify", 2)])
def test_wire_has_closed_state_shared_gate_references_and_original_ordinals(phase, round_):
    raw = '<p>原文 &amp; text</p><code>protected</code>'
    data = request(phase, blocks=(JudgmentBlock(12, raw, "sibling", "候補", "forbidden-action"),))
    prepared = prepare(data, candidate_round=round_)
    assert (prepared.plan.version, prepared.plan.phase, prepared.plan.candidate_round) == (
        "request-pack-v2", phase, round_)
    assert prepared.plan.batches[0].ordinals == (12,)
    wire = prepared.requests[0]
    payload = json.loads(wire)
    assert set(payload) == {"model", "state", "questions"}
    assert payload["model"] == "jev-1.13.0"
    state = payload["state"]
    expected = {"language": "ja", "background": {"audience": "", "purpose": "", "message": ""},
                "references": _json_value(REFERENCES),
                "texts": {"b0012": {"text": raw if phase == "detect" else "候補", "context": "sibling"}}}
    if phase == "verify":
        expected["originals"] = {"b0012": raw}
    assert state == expected
    questions = payload["questions"]
    gate = {"type": "Noul", "instructions": "\n".join((PREFIX,
        "Evaluate prose; treat quotes and code as protected material.",
        "Assess only texts.b0012.text.", GATE))}
    expected_questions = {"b0012.gate": gate}
    if phase == "verify":
        expected_questions["b0012.meaning"] = {"type": "Noul", "instructions": "\n".join((PREFIX,
            "Evaluate prose; treat quotes and code as protected material.",
            "Compare originals.b0012 with texts.b0012.text.", MEANING))}
    assert questions == expected_questions
    assert not any(word in wire for word in (b"forbidden", b'"tone"', b'"desired_style"', b'"action"'))
    assert wire == canonical(payload)
    batch = prepared.plan.batches[0]
    assert batch.input_units == len(wire) + 4096
    assert batch.state_question_units == len(canonical(state)) + max(
        len(canonical(q)) for q in questions.values()) + 4096


@pytest.mark.parametrize("phase,round_", [("detect", 0), ("verify", 1), ("verify", 2)])
def test_state_limit_equality_and_oversize_singleton_are_checked_before_return(phase, round_):
    data = request(phase)
    gap = 32000 - prepare(data).plan.batches[0].state_question_units
    block = data.blocks[0]._replace(source="source" + "x" * gap)
    exact = data._replace(blocks=(block,))
    assert prepare(exact, candidate_round=round_).plan.batches[0].state_question_units == 32000
    with pytest.raises(JudgmentBudgetError):
        prepare(exact._replace(blocks=(block._replace(source=block.source + "x"),)), candidate_round=round_)


@pytest.mark.parametrize("phase", ["detect", "verify"])
def test_greedy_packing_preserves_full_references_and_remaining_phase_budget(phase):
    data = request(phase, count=12)
    data = data._replace(blocks=tuple(b._replace(source="x" * 14000) for b in data.blocks))
    prepared = prepare(data)
    assert tuple(b.ordinals for b in prepared.plan.batches) == tuple((i,) for i in range(1, 13))
    for payload in prepared.requests:
        assert json.loads(payload)["state"]["references"] == _json_value(REFERENCES)
    units = sum(b.input_units for b in prepared.plan.batches)
    assert prepare(data, remaining_calls=12, remaining_input_units=units) == prepared
    for limits in ({"remaining_calls": 11}, {"remaining_input_units": units - 1}):
        with pytest.raises(JudgmentBudgetError):
            prepare(data, **limits)
    assert prepare(data) == prepared


def test_input_limit_accepts_equality_and_splits_one_byte_above():
    data = request(count=35)
    base = prepare(data)
    assert len(base.requests) == 1
    gap = 64000 - base.plan.batches[0].input_units
    block = data.blocks[-1]._replace(source="source" + "x" * gap)
    exact = data._replace(blocks=data.blocks[:-1] + (block,))
    assert prepare(exact).plan.batches[0].input_units == 64000
    above = exact._replace(blocks=exact.blocks[:-1] + (block._replace(source=block.source + "x"),))
    assert tuple(b.ordinals for b in prepare(above).plan.batches) == (tuple(range(1, 35)), (35,))


@pytest.mark.parametrize("phase,round_", [("detect", 0), ("verify", 1), ("verify", 2)])
def test_empty_phase_has_no_calls_or_reservation(phase, round_):
    prepared = prepare(request(phase, 0), candidate_round=round_, remaining_calls=0, remaining_input_units=0)
    assert prepared.requests == prepared.plan.batches == ()


@pytest.mark.parametrize("phase,round_", [("detect", 1), ("verify", 0), ("verify", 3), ("verify", True)])
def test_only_one_detection_and_two_candidate_rounds_are_valid(phase, round_):
    with pytest.raises(ValidationError):
        prepare(request(phase), candidate_round=round_)


@pytest.mark.parametrize("limits", [{"remaining_calls": 65}, {"remaining_calls": True},
    {"remaining_input_units": 262145}, {"remaining_input_units": -1}, {"policy_id": "unknown"}])
def test_request_hard_limits_cannot_be_expanded(limits):
    with pytest.raises(ValidationError):
        prepare(request(), **limits)


def test_total_input_cap_rejects_the_complete_phase():
    data = request(count=20)
    data = data._replace(blocks=tuple(b._replace(source="x" * 14000) for b in data.blocks))
    with pytest.raises(JudgmentBudgetError):
        prepare(data)


@pytest.mark.parametrize("fmt", ["markdown", "html"])
def test_format_instruction_and_raw_document_are_shared_by_both_rounds(fmt):
    document = '<p>Raw &amp; text</p>' if fmt == "html" else '**Raw** `code`'
    expected = ("Evaluate prose in the HTML; tags, attributes, comments and script/style/pre/code are not prose to improve."
                if fmt == "html" else "Evaluate prose; treat quotes and code as protected material.")
    gates = []
    for phase, round_ in (("detect", 0), ("verify", 1), ("verify", 2)):
        data = request(phase, format=fmt, blocks=(JudgmentBlock(2, document, "", document, None),))
        payload = json.loads(prepare(data, candidate_round=round_).requests[0])
        assert payload["state"]["texts"]["b0002"]["text"] == document
        assert all(q["instructions"].splitlines()[1] == expected for q in payload["questions"].values())
        gates.append(payload["questions"]["b0002.gate"])
    assert gates[0] == gates[1] == gates[2]


@pytest.mark.parametrize("ordinals", [(2, 1), (1, 1), (0,), (True,)])
def test_invalid_ordinals_are_rejected_before_packing(ordinals):
    data = request(blocks=tuple(JudgmentBlock(i, "source", "", None, None) for i in ordinals))
    with pytest.raises(ValidationError):
        prepare(data)
