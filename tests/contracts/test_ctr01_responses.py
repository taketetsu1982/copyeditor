import json
from pathlib import Path

import pytest
from copyeditor.providers.base import GenerationResult, SourceItem, Usage
from copyeditor.requests import ValidationError
from copyeditor.responses import parse_generation
from .harness import load_cases

pytestmark = pytest.mark.consumer("CTR-01")
CASES = load_cases(Path(__file__).resolve().parents[2] / "contracts/tools.md", "contract-case")


def item(identity="a", text="Hello.", flag=None):
    return {"id": identity, "text": text, "flag": flag}


def parse(value, ids=("a",), finish="stop", raw=False):
    return parse_generation(GenerationResult(value if raw else json.dumps(value), finish, Usage(None, None, None)),
                            tuple(SourceItem(identity, "original", "") for identity in ids))


def rejected(value, code="invalid_response", **kwargs):
    with pytest.raises(ValidationError) as caught:
        parse(value, **kwargs)
    assert caught.value.code == code and caught.value.field is None
    assert set(vars(caught.value)) == {"code", "field"} and caught.value.__context__ is None
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("finish,code", [("truncated", "generation_truncated"), ("blocked", "provider_error"),
                                        ("other", "invalid_response"), ("STOP", "invalid_response"), (None, "invalid_response"), ([], "invalid_response")])
def test_ac_02_4_ctr01_finish_precedes_json(finish, code):
    rejected("PRIVATE malformed", code, finish=finish, raw=True)


@pytest.mark.parametrize("raw", [None, b'{}', 'PRIVATE', 'null', '[]', '{"items":[],"items":[]}',
    '{"items":[{"id":"a","id":"a","text":"x","flag":null}]}',
    '{"items":[{"id":"a","text":"x","flag":{"kind":"unfixable","reason":"x","reason":"x"}}]}',
    '{"items":NaN}', '{"items":Infinity}', '['*1100])
def test_ctr01_raw_json_integrity(raw):
    rejected(raw, raw=True)


@pytest.mark.parametrize("value", [{}, {"items": []}, {"items": None}, {"items": [item()], "PRIVATE": 1},
    {"items": [item(), item()]}, {"items": [item("b")]}, {"items": [item(), item("b")]},
    *[{"items": [item(**change)]} for change in ({"identity": 1}, {"identity": "a\n"}, {"identity": "a"*65},
      {"text": None}, {"text": 3}, {"text": True}, {"text": ""}, {"text": "\ud800"}, {"text": " \u0085\u3000"},
      {"flag": {}}, {"flag": False}, {"flag": {"kind": "rejected", "reason": "x"}},
      {"flag": {"kind": "unfixable", "reason": " "}}, {"flag": {"kind": "unfixable", "reason": "x"*161}},
      {"flag": {"kind": "unfixable", "reason": "\udfff"}}, {"flag": {"kind": "unfixable", "reason": "x", "checks": []}})],
    {"items": [{"id": "a", "text": "x"}]}, {"items": [{**item(), "PRIVATE": 1}]}, {"items": [False]}])
def test_ac_02_2_ctr01_closed_model_shape(value):
    rejected(value)


@pytest.mark.parametrize("size", [15999, 16000, 16001])
def test_ac_02_2_ctr01_candidate_and_batch_limits(size):
    for values in ([item(text="𠮷"*size)], [item(text="x"*8000), item("b", "x"*(size-8000))]):
        ids = tuple(v["id"] for v in values)
        if size > 16000: rejected({"items": values}, "output_limit", ids=ids)
        else: assert sum(len(v.text) for v in parse({"items": values}, ids)) == size


def test_ac_02_2_ac_02_4_ctr01_integrity_before_limits_and_source_order():
    for bad in (item("b", " "), item("b", "x", {"kind": "unfixable", "reason": ""})):
        for values in ([item(text="x"*16001), bad], [bad, item(text="x"*16001)]):
            rejected({"items": values}, ids=("a", "b"))
    rejected({"items": [item(text="x"*16001)]}, ids=("a", "b"))
    result = parse({"items": [item("b", "\x1c"), item(text=" e\u0301 ", flag={"kind": "unfixable", "reason": "x"*160})]}, ("a", "b"))
    assert tuple(v.id for v in result) == ("a", "b") and result[0].text == " e\u0301 " and result[1].text == "\x1c"
    assert result[0].flag == {"kind": "unfixable", "reason": "x"*160}
    with pytest.raises(TypeError): result[0].flag["reason"] = "changed"
    assert parse({"items": [item("b")]}, ("b",))[0].id == "b"
    rejected({"items": [item("b")]}, ids=("a", "b"))


@pytest.mark.parametrize("case", [c for c in CASES if c["provider"]], ids=lambda c: c["name"])
def test_ctr01_real_contract_provider_batches(case):
    initial = tuple(i["id"] for i in case["input"].get("items", [{"id": "text"}]))
    retry_ids = {"partial_rejection": ("a",), "retry_integrity_discards_batch": ("a",),
                 "shared_html_retry": ("text",), "retry_merge_exceeds_16000": ("b",),
                 "retry_blank_discards_batch": ("a",), "html_incomplete_candidate": ("text",)}
    for index, batch in enumerate(case["provider"]):
        ids = initial if index == 0 else retry_ids[case["name"]]
        invalid = not batch["items"] or any(not i["text"] or not i["text"].strip() for i in batch["items"])
        over = sum(len(i["text"]) for i in batch["items"]) > 16000
        if invalid or over:
            code = "invalid_response" if invalid else "output_limit"
            assert case["expect"]["error"]["code"] == code
            rejected(batch, code, ids=ids)
        else:
            parsed = parse(batch, ids)
            assert [i.text for i in parsed] == [i["text"] for i in batch["items"]]


def test_ctr01_unicode_space_and_scalar_boundaries():
    spaces = "\t\n\v\f\r \u0085\u00a0\u1680" + "".join(map(chr, range(0x2000, 0x200b))) + "\u2028\u2029\u202f\u205f\u3000"
    for space in spaces: rejected({"items": [item(text=space)]})
    for value in ("\x1c", "\u200b", "e\u0301", "𠮷"):
        assert parse({"items": [item(text=value)]})[0].text == value
    for reason in (None, True, [], ""):
        rejected({"items": [item(flag={"kind": "unfixable", "reason": reason})]})
    for length in (159, 160):
        assert parse({"items": [item(flag={"kind": "unfixable", "reason": "x"*length})]})[0].flag
