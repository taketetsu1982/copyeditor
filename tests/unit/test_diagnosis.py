import json

import pytest

from copyeditor.diagnosis import Diagnosis, DiagnosticItem, check_no_issue, parse_diagnoses
from copyeditor.providers.base import GenerationResult, SourceItem, Usage
from copyeditor.requests import ValidationError
from copyeditor.responses import Candidate

SOURCES = (SourceItem("a", "A😀 phrase.\n", ""), SourceItem("b", "Natural.\n", ""))
ISSUE = {"id": "a", "status": "issue", "expression": "😀 phrase", "reason": "Wordy."}
NATURAL = {"id": "b", "status": "no_issue", "expression": None, "reason": None}


def parse(rows, sources=SOURCES, finish="stop"):
    raw = rows if isinstance(rows, str) else json.dumps({"diagnoses": rows})
    return parse_diagnoses(GenerationResult(raw, finish, Usage(None, None, None)), sources)


def rejected(rows, code="invalid_response", sources=SOURCES):
    with pytest.raises(ValidationError) as caught:
        parse(rows, sources)
    assert caught.value.code == code
    assert caught.value.__context__ is None


def test_ctr01_diagnoses_are_immutable_and_ordered_by_original_id():
    result = parse([NATURAL, ISSUE])
    assert result == (DiagnosticItem("a", Diagnosis("issue", "😀 phrase", "Wordy.")),
                      DiagnosticItem("b", Diagnosis("no_issue", None, None)))
    with pytest.raises(AttributeError):
        result[0].diagnosis.reason = "Changed"
    assert result[0].diagnosis.expression in SOURCES[0].text


@pytest.mark.parametrize("rows", [[], [ISSUE], [ISSUE, ISSUE], [ISSUE, NATURAL, NATURAL],
    [ISSUE, {**NATURAL, "id": "extra"}], [ISSUE, {**NATURAL, "id": []}],
    [ISSUE, {**NATURAL, "unknown": True}], [ISSUE, {"id": "b", "status": "no_issue"}],
    [ISSUE, None], [ISSUE, {**NATURAL, "expression": "Natural"}],
    [ISSUE, {**NATURAL, "reason": "Fine"}], [ISSUE, {**NATURAL, "status": False}]])
def test_ctr01_rejects_incomplete_or_open_diagnosis_shapes(rows):
    rejected(rows)


@pytest.mark.parametrize("field,value", [
    ("expression", None), ("expression", 1), ("expression", ""), ("expression", "\n"),
    ("expression", "a😀 phrase"), ("expression", "\ud800"),
    ("reason", None), ("reason", []), ("reason", ""), ("reason", "\u0085\u3000"),
    ("reason", "\udfff"), ("status", "other"), ("status", {})])
def test_ctr01_issue_strings_are_unicode_nonblank_and_exact_substrings(field, value):
    rejected([{**ISSUE, field: value}, NATURAL])


@pytest.mark.parametrize("raw", [None, "null", "[]", "{}", '{"diagnoses":null}',
    '{"diagnoses":[],"diagnoses":[]}', '{"diagnoses":[],"other":0}',
    '{"diagnoses":[{"id":"a","id":"a"}]}', '{"diagnoses":NaN}',
    '{"diagnoses":', '[' * 2000])
def test_ctr01_invalid_json_does_not_retain_decoder_context(raw):
    result = GenerationResult(raw, "stop", Usage(None, None, None))
    with pytest.raises(ValidationError) as caught:
        parse_diagnoses(result, SOURCES)
    assert caught.value.code == "invalid_response"
    assert caught.value.__context__ is None


@pytest.mark.parametrize("finish,code", [("truncated", "generation_truncated"),
    ("blocked", "provider_error"), ("other", "invalid_response")])
def test_ctr01_finish_precedes_diagnostic_validation(finish, code):
    with pytest.raises(ValidationError) as caught:
        parse([], finish=finish)
    assert caught.value.code == code


@pytest.mark.parametrize("expression,reason,code", [(160, 320, None), (161, 320, "output_limit"),
    (160, 321, "output_limit")])
def test_ctr01_field_caps_count_code_points(expression, reason, code):
    sources = (SourceItem("a", "😀" * 161, ""),)
    rows = [{**ISSUE, "expression": "😀" * expression, "reason": "文" * reason}]
    if code:
        rejected(rows, code, sources)
    else:
        assert len(parse(rows, sources)[0].diagnosis.expression) == 160


@pytest.mark.parametrize("extra", [0, 1])
def test_ctr01_total_diagnosis_cap_is_inclusive(extra):
    sources = tuple(SourceItem(str(i), "x" * 160, "") for i in range(18))
    rows = [{"id": str(i), "status": "issue", "expression": "x" * 160, "reason": "r" * 320}
            for i in range(17)]
    rows.append({"id": "17", "status": "issue", "expression": "x", "reason": "r" * (31 + extra)})
    if extra:
        rejected(rows, "output_limit", sources)
    else:
        assert len(parse(rows, sources)) == 18


def test_ctr01_integrity_precedes_length_across_all_items():
    oversized = {**ISSUE, "reason": "r" * 321}
    rejected([oversized, {**NATURAL, "reason": "must be null"}])
    rejected([oversized])
    rejected([oversized, NATURAL], "output_limit")


@pytest.mark.parametrize("flag", [None, {"kind": "unfixable", "reason": "Cannot change."}])
@pytest.mark.parametrize("replacement", ["Natural.", "Natural. \n", "Changed.", "x" * 16001])
def test_inv9_no_issue_change_is_invalid_even_with_flag_or_excess_length(flag, replacement):
    frozen = parse([ISSUE, NATURAL])
    with pytest.raises(ValidationError) as caught:
        check_no_issue(frozen, SOURCES[1:], (Candidate("b", replacement, flag),))
    assert caught.value.code == "invalid_response"


def test_inv9_same_invariant_covers_candidate_retry_and_final_merge():
    frozen = parse([ISSUE, NATURAL])
    for candidates in [(Candidate("b", SOURCES[1].text, None),),
                       (Candidate("b", SOURCES[1].text, {"kind": "unfixable"}),)]:
        check_no_issue(frozen, SOURCES[1:], candidates)
        check_no_issue(frozen, SOURCES, (Candidate("a", "Changed.", None), *candidates))
    for candidates in [(), (Candidate("b", SOURCES[1].text, None),) * 2,
                       (Candidate("extra", SOURCES[1].text, None),)]:
        with pytest.raises(ValidationError):
            check_no_issue(frozen, SOURCES[1:], candidates)
    with pytest.raises(ValidationError):
        check_no_issue(frozen[:1], SOURCES[1:], (Candidate("b", SOURCES[1].text, None),))
