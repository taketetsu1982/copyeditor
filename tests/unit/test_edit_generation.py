"""AC-02-4/07-11/07-12: candidates and one-line diagnoses share one response."""
import json

import pytest
from jsonschema import Draft202012Validator

from copyeditor.edit_generation import generation_schema, parse_generation
from copyeditor.prompt import edit_contents, edit_system_instruction
from copyeditor.providers.base import Background, EditGenerationInput, GenerationResult, SourceItem, Usage
from copyeditor.requests import ValidationError


def originals(count=1):
    return tuple(SourceItem(f"i{i}", "original", "context") for i in range(count))


def item(i=0, **changes):
    return dict(id=f"i{i}", text="candidate", flag=None, diagnosis="Shortened wording.") | changes


def result(items, finish="stop"):
    return GenerationResult(json.dumps({"items": items}), finish, Usage(0, 0, 0))


def parse(items, *, count=1, stage="rewrite", finish="stop"):
    return parse_generation(result(items, finish), originals(count), stage=stage)


@pytest.mark.parametrize("stage", ["polish", "rewrite"])
def test_response_keeps_candidate_diagnosis_pairs_in_original_order(stage):
    entries = [item(1, diagnosis=None if stage == "polish" else "Second."),
               item(0, diagnosis=None if stage == "polish" else "First.")]
    parsed = parse(entries, count=2, stage=stage)
    assert [p.id for p in parsed] == ["i0", "i1"]
    assert [p.diagnosis for p in parsed] == ([None, None] if stage == "polish" else ["First.", "Second."])
    assert Draft202012Validator(generation_schema(stage)).is_valid({"items": entries})
    changed = parse([item(text="replacement", diagnosis="Replacement explanation.")])
    assert (changed[0].text, changed[0].diagnosis) == ("replacement", "Replacement explanation.")


@pytest.mark.parametrize("diagnosis", [None, "", " \u3000", "x\ny", "x\ry", "\ud800", {}, 3])
def test_rewrite_rejects_non_line_or_non_scalar_diagnosis(diagnosis):
    with pytest.raises(ValidationError) as error:
        parse([item(diagnosis=diagnosis)])
    assert error.value.code == "invalid_response"


def test_polish_requires_null_diagnosis():
    with pytest.raises(ValidationError):
        parse([item()], stage="polish")


@pytest.mark.parametrize("size,accepted", [(320, True), (321, False)])
def test_diagnosis_per_item_length_boundary(size, accepted):
    if accepted:
        assert len(parse([item(diagnosis="語" * size)])[0].diagnosis) == size
    else:
        with pytest.raises(ValidationError) as error:
            parse([item(diagnosis="語" * size)])
        assert error.value.code == "output_limit"


@pytest.mark.parametrize("extra", [0, 1])
def test_diagnosis_aggregate_limit_counts_unicode_code_points(extra):
    entries = [item(i, diagnosis="語" * 320) for i in range(25)]
    entries.append(item(25, diagnosis="語" * (192 + extra)))
    if not extra:
        assert sum(len(p.diagnosis) for p in parse(entries, count=26)) == 8192
    else:
        with pytest.raises(ValidationError) as error:
            parse(entries, count=26)
        assert error.value.code == "output_limit"


@pytest.mark.parametrize("bad", [item(id="unknown"), item(text=" \t"), item(text="\ud800"),
    item(diagnosis="x\ny"), item(extra="private"), item(flag={"kind": "rejected", "reason": "x"}),
    item(flag={"kind": "unfixable", "reason": "x"}), item(flag={"kind": "unfixable", "reason": " "})])
def test_all_item_integrity_precedes_any_length_excess(bad):
    with pytest.raises(ValidationError) as error:
        parse([item(0, diagnosis="x" * 321, text="x" * 16001), bad | {"id": bad["id"] if bad["id"] == "unknown" else "i1"}], count=2)
    assert error.value.code == "invalid_response"


def test_unfixable_keeps_exact_original_and_same_response_diagnosis():
    parsed = parse([item(text="original", flag={"kind": "unfixable", "reason": "Ambiguous."})])[0]
    assert parsed.text == "original" and parsed.diagnosis == "Shortened wording."
    with pytest.raises(TypeError):
        parsed.flag["reason"] = "changed"


@pytest.mark.parametrize("finish,code", [("truncated", "generation_truncated"), ("blocked", "provider_error"),
                                         ("unknown", "invalid_response")])
def test_finish_precedes_shape_and_size(finish, code):
    with pytest.raises(ValidationError) as error:
        parse_generation(GenerationResult("invalid private JSON", finish, Usage(0, 0, 0)), originals(), stage="rewrite")
    assert error.value.code == code


@pytest.mark.parametrize("raw", ['{"items":[],"extra":1}', '{"items":[],"items":[]}',
                                 '{"items":[NaN]}', 'private invalid JSON'])
def test_invalid_json_and_closed_shape_hide_decoder_context(raw):
    with pytest.raises(ValidationError) as error:
        parse_generation(GenerationResult(raw, "stop", Usage(0, 0, 0)), originals(), stage="rewrite")
    assert error.value.code == "invalid_response" and error.value.__context__ is None


@pytest.mark.parametrize("entries", [[item(), item()], [], [dict(id="i0", text="text", flag=None)]])
def test_duplicate_missing_ids_and_missing_diagnosis_are_invalid(entries):
    with pytest.raises(ValidationError):
        parse(entries)


@pytest.mark.parametrize("stage", ["polish", "rewrite"])
def test_new_generation_input_has_no_frozen_diagnosis_and_preserves_editing_tone(stage):
    background = Background("readers", "purpose", "natural", "message")
    instruction = edit_system_instruction("common", "language", ("term",), stage)
    data = EditGenerationInput(originals(), "en", "text", background, instruction, stage)
    payload = json.loads(edit_contents(data))
    assert set(data._fields) == {"items", "language", "format", "background", "system_instruction", "stage"}
    assert set(payload) == {"items", "language", "format", "background"}
    assert payload["background"]["tone"] == "natural" and payload["items"][0]["text"] == "original"
    assert "Return diagnosis" in instruction or "diagnosis line" in instruction
    assert "diagnose" not in instruction and "frozen" not in instruction
    with pytest.raises(ValidationError):
        edit_contents(data._replace(stage="diagnose"))
    with pytest.raises(ValidationError):
        generation_schema("diagnose")


@pytest.mark.parametrize("length,code", [(16000, None), (16001, "output_limit")])
def test_candidate_body_limit_follows_integrity(length, code):
    if code is None:
        assert len(parse([item(text="x" * length)])[0].text) == length
    else:
        with pytest.raises(ValidationError) as error:
            parse([item(text="x" * length)])
        assert error.value.code == code
