from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from copyeditor.requests import ValidationError, edit_input_schema, input_schema, parse_edit_request, parse_request
from tests.contracts.test_ctr01_requests import settings


@pytest.mark.parametrize("degree", [None, "polish", "rewrite"])
@pytest.mark.parametrize("language", ["ja", "en", "zh"])
@pytest.mark.parametrize("format", ["text", "markdown", "html"])
def test_ctr01_internal_degree_and_languages_preserve_input(settings, degree, language, format):
    arguments = {"text": "<p>文😀</p>\n" if format == "html" else " 文😀\n", "format": format, "language": language}
    if degree is not None:
        arguments["degree"] = degree
    before = deepcopy(arguments)
    result = parse_edit_request("polish_text", arguments, *settings)
    assert result.degree == (degree or "polish")
    assert result.language == language and result.format == format
    assert result.items[0].text == arguments["text"]
    assert arguments == before
    assert Draft202012Validator(edit_input_schema("polish_text", *settings)).is_valid(arguments)
    if degree is not None:
        assert not Draft202012Validator(input_schema("polish_text", *settings)).is_valid(arguments)
        with pytest.raises(ValidationError):
            parse_request("polish_text", arguments, *settings)
    else:
        assert parse_request("polish_text", arguments, *settings) == result


@pytest.mark.parametrize("degree", [None, True, 1, [], {}, "", "Rewrite", "rewrite\n", "\ud800"])
def test_ctr01_internal_degree_is_closed_and_not_coerced(settings, degree):
    with pytest.raises(ValidationError) as error:
        parse_edit_request("polish_text", {"text": "x", "degree": degree}, *settings)
    assert (error.value.code, error.value.field) == ("invalid_input", "degree")


@pytest.mark.parametrize("degree", ["polish", "rewrite", None])
def test_ctr01_lint_never_accepts_degree(settings, degree):
    arguments = {"text": "x", "degree": degree}
    with pytest.raises(ValidationError):
        parse_edit_request("lint_text", arguments, *settings)
    assert not Draft202012Validator(edit_input_schema("lint_text", *settings)).is_valid(arguments)


@pytest.mark.parametrize("delta", [0, 1])
@pytest.mark.parametrize("kind", ["body", "count", "context", "context_total", "context_background", "background"])
def test_ac_07_7_ctr01_decoded_budget_is_checked_before_any_batch_split(settings, delta, kind):
    arguments = {"items": [{"id": str(i), "text": "x"} for i in range(5)], "degree": "rewrite", "language": "en"}
    if kind == "body":
        for item in arguments["items"]: item["text"] = "😀" * 2400
        arguments["items"][-1]["text"] += "x" * delta
    elif kind == "count":
        arguments["items"] = [{"id": str(i), "text": "x"} for i in range(32 + delta)]
    elif kind == "context": arguments["items"][0]["context"] = "x" * (1000 + delta)
    elif kind == "context_total":
        for item in arguments["items"]: item["context"] = "x" * 800
        arguments["items"][-1]["context"] += "x" * delta
    elif kind == "context_background":
        arguments.update(audience="x" * 1000, purpose="x" * 1000, tone="x" * 1000, message="x" * 1000)
        arguments["items"][0]["context"] = "x" * delta
    else: arguments["audience"] = "x" * (1000 + delta)
    if delta:
        with pytest.raises(ValidationError) as error:
            parse_edit_request("polish_text", arguments, *settings)
        assert error.value.code == "input_limit"
    else:
        assert parse_edit_request("polish_text", arguments, *settings).degree == "rewrite"


@pytest.mark.parametrize("arguments", [None, [], {"text": "x", "items": [], "degree": "rewrite"},
    {"items": [{"id": "a", "text": "<p>x</p>"}], "format": "html", "degree": "rewrite"},
    {"text": "x", "degree": "rewrite", "diagnosis": {}},
    {"items": [{"id": "a", "text": "x", "degree": "rewrite"}], "degree": "rewrite"}])
def test_ctr01_rewrite_does_not_accept_ambiguous_routes_or_per_item_instructions(settings, arguments):
    with pytest.raises(ValidationError):
        parse_edit_request("polish_text", arguments, *settings)
