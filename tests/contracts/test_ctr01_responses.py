import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from copyeditor.providers.base import GenerationResult, SourceItem, Usage
from copyeditor.requests import MESSAGES, ValidationError
from copyeditor.responses import output_schema, parse_generation
from .harness import assert_subset, load_cases

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


def complete_subset(base, subset):
    if isinstance(subset, dict):
        return {**base, **{k: complete_subset(base.get(k), v) for k, v in subset.items()}}
    if isinstance(subset, list):
        return [complete_subset(base[i] if i < len(base) else None, v) for i, v in enumerate(subset)]
    return deepcopy(subset)


def public_fixture(case):
    expect = case["expect"]
    lint = case["tool"] == "lint_text"
    calls = expect.get("model_calls", len(case["provider"]))
    base = dict(status="ok", schema_version=1, language="en", rules_version="1", common_version="1",
                model=None if lint else "test-model", usage=dict.fromkeys(("input_tokens", "output_tokens", "total_tokens"), None if calls else 0),
                cost=None, latency_ms=0, model_calls=calls)
    if expect["status"] == "error":
        code = expect["error"]["code"]
        base.update(error=dict(code=code, message=MESSAGES[code], field=None), model_called=calls > 0,
                    regeneration_attempted=calls > 1)
    else:
        base.update(protected_terms_checked=0, preservation=None if lint else {"length_ratio": {"min": 0.5, "max": 2}})
        result = dict(text="Hello.", flag=None, regenerated=False, protected_terms=[], findings=[], findings_truncated=False)
        if lint:
            base.update(findings=[], findings_truncated=False)
        elif "items" in case["input"]:
            entries = expect.get("items", case["provider"][0]["items"])
            base["items"] = [{**deepcopy(result), "id": entry["id"], "text": entry["text"]} for entry in entries]
            for value, entry in zip(base["items"], entries):
                if entry.get("flag"):
                    value["flag"] = dict(kind="rejected", reason="Preservation checks failed.", checks=[])
        else:
            base.update(result)
    return complete_subset(base, expect)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_ac_02_2_ctr01_complete_contract_output_shapes(case):
    schema = output_schema(case["tool"])
    Draft202012Validator.check_schema(schema)
    payload = public_fixture(case)
    assert_subset(payload, case["expect"])
    Draft202012Validator(schema).validate(payload)


@pytest.mark.parametrize("code", MESSAGES)
def test_ac_02_4_ctr01_error_schema_fixed_messages_and_fields(code):
    payload = public_fixture(dict(tool="polish_text", provider=[], expect={"status": "error", "error": {"code": code}}))
    for tool in ("polish_text", "lint_text"):
        validator = Draft202012Validator(output_schema(tool))
        assert validator.is_valid(payload)
        for key, value in (("code", "PRIVATE"), ("message", "PRIVATE"), ("field", "items[0].PRIVATE")):
            bad = deepcopy(payload); bad["error"][key] = value
            assert not validator.is_valid(bad)
        for field in ("text", "items[0].text", "items[31].context", "language", None):
            payload["error"]["field"] = field
            assert validator.is_valid(payload) == (field is None or code in ("invalid_input", "unsupported_language", "input_limit"))
        payload["error"]["field"] = None


def object_paths(value, path=()):
    if isinstance(value, dict):
        yield path
        for k, v in value.items(): yield from object_paths(v, path + (k,))
    elif isinstance(value, list):
        for k, v in enumerate(value): yield from object_paths(v, path + (k,))


def at(value, path):
    for key in path: value = value[key]
    return value


@pytest.mark.parametrize("route", ["text", "items"])
def test_ctr01_nested_closed_required_and_types(route):
    payload = public_fixture(CASES[0])
    payload.update(cost={"amount": "0.000001", "currency": "USD"}, usage=dict.fromkeys(("input_tokens", "output_tokens", "total_tokens"), 0))
    payload["flag"] = dict(kind="unfixable", reason="Cannot edit.", checks=[])
    payload["findings"] = [dict(rule_id="en-test", start=0, end=1, matched="H", matched_truncated=False, message="Example.")]
    if route == "items":
        keys = ("text", "flag", "regenerated", "protected_terms", "findings", "findings_truncated")
        payload["items"] = [{"id": "a", **{key: payload.pop(key) for key in keys}}]
    validator = Draft202012Validator(output_schema("polish_text"))
    assert validator.is_valid(payload)
    for path in object_paths(payload):
        for key in (*at(payload, path), "PRIVATE"):
            bad = deepcopy(payload); target = at(bad, path)
            if key == "PRIVATE": target[key] = 1
            else: del target[key]
            assert not validator.is_valid(bad), (path, key)
        for key, value in at(payload, path).items():
            for wrong in (None, {}, [], True, 42, "PRIVATE\ud800"):
                if type(wrong) is type(value) or type(value) is float and type(wrong) is int or wrong is None and key in ("model", "cost", "flag", "input_tokens", "output_tokens", "total_tokens"): continue
                bad = deepcopy(payload); at(bad, path)[key] = wrong
                assert not validator.is_valid(bad), (path, key, wrong)
    for key, wrong in (("text", " \u3000"), ("text", "x"*16001), ("regenerated", None), ("items", [])):
        assert not validator.is_valid({**payload, key: wrong})
    assert not Draft202012Validator(output_schema("lint_text")).is_valid(payload)
    with pytest.raises(ValidationError): output_schema("unknown")


@pytest.mark.parametrize("change", [
    {"flag": {"kind": "rejected", "reason": "Preservation checks failed.", "checks": []}},
    {"flag": {"kind": "rejected", "reason": "PRIVATE", "checks": ["numbers"]}},
    {"flag": {"kind": "unfixable", "reason": "x", "checks": ["numbers"]}},
    {"flag": {"kind": "unfixable", "reason": "x"*161, "checks": []}},
    {"cost": {"amount": "1.0", "currency": "USD"}}, {"cost": {"amount": "0.000000", "currency": "usd"}},
    {"text": "\ud800"}, {"language": "en\n"}, {"schema_version": True}, {"model_calls": 3}, {"model": None},
    {"usage": {"input_tokens": -1, "output_tokens": 0, "total_tokens": 0}},
    {"preservation": {"length_ratio": {"min": 0, "max": 2}}}])
def test_ctr01_schema_constraints(change):
    assert not Draft202012Validator(output_schema("polish_text")).is_valid({**public_fixture(CASES[0]), **change})


def test_ctr01_error_closed_and_lint_constants():
    error = public_fixture(CASES[1])
    validator = Draft202012Validator(output_schema("polish_text"))
    for key in error["error"]:
        bad = deepcopy(error); del bad["error"][key]
        assert not validator.is_valid(bad)
    assert not validator.is_valid({**error, "error": {**error["error"], "PRIVATE": 0}})
    lint = public_fixture(next(c for c in CASES if c["tool"] == "lint_text"))
    for key, value in (("model", "x"), ("model_calls", 1), ("protected_terms_checked", 1), ("preservation", {})):
        assert not Draft202012Validator(output_schema("lint_text")).is_valid({**lint, key: value})
