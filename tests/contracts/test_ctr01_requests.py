from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from copyeditor.config import load_config
from copyeditor.providers.base import Background, SourceItem
from copyeditor.requests import ValidationError, input_schema, parse_request
from copyeditor.rules import load_rules
from .harness import load_cases

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.consumer("CTR-01")


@pytest.fixture(scope="module")
def settings():
    return load_config(ROOT / "absent-config", {"GOOGLE_CLOUD_PROJECT": "test"}), load_rules(ROOT / "rules", None)


def parse(settings, value, tool="polish_text"):
    return parse_request(tool, value, *settings)


def fail(settings, value, code="invalid_input", field=None, tool="polish_text"):
    with pytest.raises(ValidationError) as error:
        parse(settings, value, tool)
    assert (error.value.code, error.value.field) == (code, field)
    assert "PRIVATE" not in str(error.value) and "PRIVATE" not in repr(vars(error.value))


@pytest.mark.parametrize("case", load_cases(ROOT / "contracts/tools.md", "contract-case"), ids=lambda c: c["name"])
def test_ctr01_real_contract_inputs(settings, case):
    if "text" in case["input"] and "items" in case["input"]:
        fail(settings, case["input"], case["expect"]["error"]["code"])
        return
    result = parse(settings, case["input"], case["tool"])
    assert result.language == case["input"].get("language", "ja")
    assert [i.text for i in result.items] == ([case["input"]["text"]] if "text" in case["input"] else [i["text"] for i in case["input"]["items"]])
    Draft202012Validator(input_schema(case["tool"], *settings)).validate(case["input"])


@pytest.mark.parametrize("value,field", [({}, None), ({"text": "x", "items": []}, None), ({"text": "x", "items": [], "format": "html"}, None), ({"text": None}, "text"),
    ({"text": 1}, "text"), ({"text": True}, "text"), ({"text": b"x"}, "text"), ({"text": "x", "PRIVATE": 1}, None),
    ({"text": ""}, "text"), ({"text": "\t\n\r \u0085\u00a0\u1680\u2000\u202f\u3000"}, "text"),
    ({"text": "\ud800"}, "text"), ({"text": "x", "tone": None}, "tone"), ({"text": "x", "language": None}, "language"),
    ({"text": "x", "language": "JA"}, "language"), ({"text": "x", "format": "PRIVATE"}, "format"),
    ({"items": ()}, "items"), ({"items": [None]}, "items[0]"), ({"items": [{"id": "x", "text": "x", "PRIVATE": 1}]}, "items[0]"),
    ({"items": [{"id": "x", "text": "x"}]*2}, "items[1].id"), ({"items": [{"id": "a\n", "text": "x"}]}, "items[0].id"),
    ({"items": [{"id": "x", "text": "x"}], "format": "html"}, "format")])
def test_ac_02_1_ac_02_2_ctr01_strict_shape(settings, value, field):
    fail(settings, value, field=field)
    assert not Draft202012Validator(input_schema("polish_text", *settings)).is_valid(value) or field == "items[1].id"


@pytest.mark.parametrize("delta", [-1, 0, 1])
@pytest.mark.parametrize("kind,limit,field", [("text",12000,"text"), ("count",32,"items"), ("context",1000,"items[0].context"),
    *[(f,1000,f) for f in Background._fields], ("bodies",12000,"items"), ("contexts",4000,"items"), ("combined",4000,None)])
def test_ac_02_2_ac_02_5_ctr01_budget_edges(settings, delta, kind, limit, field):
    n = limit + delta
    value = {"text": "𠮷" * n} if kind == "text" else {"text": "x"}
    if kind in Background._fields:
        value[kind] = "x" * n
    if kind == "count":
        value = {"items": [{"id": str(i), "text": "x"} for i in range(n)]}
    if kind in ("context", "bodies", "contexts", "combined"):
        value = {"items": [{"id": str(i), "text": "x", "context": ""} for i in range(5)]}
        if kind == "context": value["items"][0]["context"] = "x" * n
        if kind == "bodies":
            for i, item in enumerate(value["items"]): item["text"] = "x" * (n//5 + (i < n%5))
        if kind == "contexts":
            for i, item in enumerate(value["items"]): item["context"] = "x" * (n//5 + (i < n%5))
        if kind == "combined":
            value.update(audience="a"*1000, purpose="b"*1000, tone="c"*1000)
            value["items"][0]["context"], value["items"][1]["context"] = "x"*999, "x"*(n-3999)
    if delta > 0: fail(settings, value, "input_limit", field)
    else: assert parse(settings, value).items


def test_ac_02_1_ac_02_2_ac_02_5_ac_02_6_ctr01_defaults_order_and_schema(settings):
    result = parse(settings, {"text": " e\u0301\x1c\u200b ", "audience": ""})
    assert result.items == (SourceItem("text", " e\u0301\x1c\u200b ", ""),) and result.background == Background("", "", "", "")
    assert result.language == "ja" and result.format == "text"
    with pytest.raises(AttributeError): result.language = "en"
    for language in ("ja", "en", "zh"):
        assert parse(settings, {"text": "x", "language": language}).language == language
    fail(settings, {"text": "x"*12001, "language": "fr"}, "unsupported_language", "language")
    fail(settings, {"text": None, "language": "fr"}, field="text")
    fail(settings, {"items": [{"id": "a", "text": None}, {"id": "!", "text": "x"}]}, field="items[1].id")
    for key in ("format", "items", *Background._fields): fail(settings, {"text": "x", key: "PRIVATE"}, tool="lint_text")
    for tool in ("polish_text", "lint_text"):
        schema = input_schema(tool, *settings)
        Draft202012Validator.check_schema(schema)
        assert schema["properties"]["language"]["enum"] == sorted(settings[1].languages)
        assert schema["properties"]["language"]["default"] == "ja"
    assert parse(settings, {"text": "x"*12000, **dict.fromkeys(Background._fields, "a"*1000)}).items
    for size in (0, 65): fail(settings, {"items": [{"id": "a"*size, "text": "x"}]}, field="items[0].id")
    assert parse(settings, {"items": [{"id": "a"*64, "text": "x"}]}).items
    fail(settings, {"items": []}, "input_limit", "items")


def test_ctr01_unicode_optional_fields_and_resolved_default(settings):
    for field in Background._fields:
        for value in (None, True, [], "\udfff"):
            fail(settings, {"text": "x", field: value}, field=field)
    for field in ("id", "text", "context"):
        for value in (None, False, {}, "\ud800"):
            item = {"id": "a", "text": "x", "context": ""}
            item[field] = value
            fail(settings, {"items": [item]}, field="items[0]." + field)
    for size in (1, 63, 64): assert parse(settings, {"items": [{"id": "a"*size, "text": "x"}]}).items
    config = load_config(ROOT / "absent-config", {"GOOGLE_CLOUD_PROJECT": "test", "COPYEDITOR_DEFAULT_LANGUAGE": "en"})
    assert parse_request("lint_text", {"text": "x"}, config, settings[1]).language == "en"
    assert input_schema("lint_text", config, settings[1])["properties"]["language"]["default"] == "en"


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_ctr01_total_budget_and_size_precedence(settings, delta):
    value = {"text": "x"*(12000+delta), **dict.fromkeys(Background._fields, "a"*1000)}
    if delta > 0: fail(settings, value, "input_limit", "text")
    else: assert parse(settings, value).items
    fail(settings, {"items": [{"id": "a", "text": "x"*6001, "context": "x"*1001},
                             {"id": "b", "text": "x"*6000}]}, "input_limit", "items")


def test_ctr01_all_fixed_errors_match_contract():
    rows = [line.split("|") for line in (ROOT / "contracts/tools.md").read_text().splitlines() if line.startswith("| `")]
    for row in rows:
        code, message = row[1].strip().strip("`"), row[2].strip().strip("`")
        error = ValidationError(code, None)
        assert str(error) == message and vars(error) == {"code": code, "field": None}
    with pytest.raises(ValueError, match="^$"): ValidationError("unknown")
