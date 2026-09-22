import json

import pytest

from .test_ctr05_examples import ROOT, adapter, converter, get_assert
from copyeditor.rules import load_rules

CASES = converter["load_examples"](ROOT / "examples", load_rules(ROOT / "rules", None))
EXPECTED = {f"rewrite-{index:02}" for index in range(1, 9)}
REWRITE = [case for case in CASES if case["language"] == "ja" and case["id"] in EXPECTED]


def test_ac_07_8_ac_07_13_ctr05_first_fixed_subset_retains_its_problems_and_invariants(tmp_path):
    assert {case["id"] for case in REWRITE} == EXPECTED
    assert all(case["degree"] == "rewrite" and case["must_change"] and case["bad"] != case["good"] for case in REWRITE)
    families = [case["rewrite_expectations"]["problems"][0].split(":")[0] for case in REWRITE]
    assert families == ["Fashionable wording"] * 3 + ["Aphoristic ending"] * 3 + ["Patterned repetition"] * 2
    assert {case["format"] for case in REWRITE} == {"text", "markdown", "html"}
    assert all(case["rewrite_expectations"]["invariants"] and case["lint"] is None for case in REWRITE)
    assert any(case.get("regression") == "protected-terms-overreach" for case in CASES)
    output = tmp_path / "subset.json"
    converter["convert"](output, REWRITE)
    assert [test["vars"] for test in json.loads(output.read_text())["tests"]] == REWRITE


@pytest.mark.parametrize("identity,mutation,code", [
    ("rewrite-02", "number", "rejected"), ("rewrite-03", "html", "changed_structure"),
    ("rewrite-07", "unchanged", "unchanged")])
@pytest.mark.asyncio
async def test_ac_07_3_ac_07_8_ctr05_synthetic_mutations_do_not_pass(identity, mutation, code):
    case = next(case for case in REWRITE if case["id"] == identity)
    good = case["good"]
    if mutation == "number": good = good.replace("1", "2")
    elif mutation == "html": good = good.replace("<p>", "<div>").replace("</p>", "</div>")
    else: good = case["bad"]
    assert good != case["good"]
    changed = case | dict(good=good)
    response = await adapter.call_api("", {}, {"vars": changed})
    result = json.loads(response["output"])
    assert not get_assert(response["output"], {"vars": case})
    if code == "changed_structure":
        assert result["status"] == "ok" and result["text"] == good
        assert get_assert(response["output"], {"vars": changed})
    elif code == "rejected":
        assert result["flag"]["kind"] == code and result["flag"]["checks"] == ["numbers"]
        assert result["text"] == good
    else:
        assert result["status"] == "ok" and result["text"] == case["bad"]


SECOND_IDS = {f"rewrite-{index:02}" for index in range(9, 17)}
SECOND = [case for case in CASES if case["language"] == "ja" and case["id"] in SECOND_IDS]


def test_ac_07_8_ctr05_remaining_problem_types_and_natural_subset():
    assert {case["id"] for case in SECOND} == SECOND_IDS
    problems, natural = SECOND[:4], SECOND[4:]
    assert [case["rewrite_expectations"]["problems"][0].split(":")[0] for case in problems] == [
        "Patterned repetition", "Literal translation", "Literal translation", "Literal translation"]
    assert all(case["must_change"] and case["bad"] != case["good"] for case in problems)
    assert all(case["must_change"] is False and case["bad"] == case["good"] and
               case["rewrite_expectations"]["problems"] == [] for case in natural)
    assert sum(any("polite register" in value for value in case["rewrite_expectations"]["invariants"]) for case in natural) == 3
    assert sum(any("plain register" in value for value in case["rewrite_expectations"]["invariants"]) for case in natural) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [f"rewrite-{index:02}" for index in range(13, 17)])
async def test_ac_07_3_ctr05_natural_source_cannot_gain_even_trailing_whitespace(identity):
    case = next(case for case in SECOND if case["id"] == identity)
    response = await adapter.call_api("", {}, {"vars": case | dict(good=case["bad"] + " ")})
    result = json.loads(response["output"])
    assert result["status"] == "ok" and result["providers"][0]["model_calls"] == 1
    assert result["text"] == case["bad"] + " " and result["diagnosis"]
    assert not get_assert(response["output"], {"vars": case})


FIXED_IDS = {f"rewrite-{index:02}" for index in range(1, 25)}
FIXED = [case for case in CASES if case["language"] == "ja" and case["id"] in FIXED_IDS]


def test_ac_07_8_ac_07_13_ctr05_fixed_twenty_four_examples_cover_the_frozen_population():
    from collections import Counter
    assert {case["id"] for case in FIXED} == FIXED_IDS and len(FIXED) == 24
    assert all(case["degree"] == "rewrite" for case in FIXED)
    assert sum(case["must_change"] for case in FIXED) == 18
    natural = [case for case in FIXED if not case["must_change"]]
    assert len(natural) == 6 and all(case["bad"] == case["good"] for case in natural)
    for register in ("polite register", "plain register"):
        assert sum(any(register in value for value in case["rewrite_expectations"]["invariants"]) for case in natural) == 3
    assert Counter(case["rewrite_expectations"]["problems"][0].split(":")[0] for case in FIXED[:12]) == dict.fromkeys(
        ["Fashionable wording", "Aphoristic ending", "Patterned repetition", "Literal translation"], 3)
    labels = [case["rewrite_expectations"]["invariants"][0].split(":")[0] for case in FIXED[18:]]
    assert labels == ["Subject binding", "Condition", "Negation", "Promise strength", "Protected term", "URL and variable"]
    assert sum(case["format"] == "html" for case in FIXED) >= 2
    assert any(case["format"] == "markdown" for case in FIXED)
    assert all(case["must_change"] and case["rewrite_expectations"]["problems"] for case in FIXED[18:])


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["text", "markdown"])
async def test_ac_07_6_ac_07_13_ctr05_fixed_text_items_keep_order_and_frozen_diagnoses(format):
    from copyeditor.providers.base import GenerationResult, Usage
    from copyeditor.requests import parse_edit_request
    from copyeditor.edit_protocol import validate_final
    from copyeditor.service import Service
    selected = [case for case in FIXED if case["format"] == format]
    by_id = {case["id"]: case for case in selected}
    config, snapshot = adapter.environment(selected[0], "fixture")
    snapshot = load_rules(ROOT / "rules", None, tuple(term for case in selected for term in case["protected_terms"]))
    calls = []
    class Batch(adapter.FixtureProvider):
        async def generate(self, data):
            calls.append(data)
            key = "items"
            values = []
            for item in reversed(data.items):
                case = by_id[item.id]
                single = adapter.FixtureProvider(case["good"], no_issue=not case["must_change"])
                value = await single.generate(data._replace(items=(item,)))
                values.extend(json.loads(value.raw_json)[key])
            return GenerationResult(json.dumps({key: values}), "stop", Usage(0, 0, 0))
    arguments = dict(items=[dict(id=case["id"], text=case["bad"]) for case in selected],
        language="ja", degree="rewrite", format=format)
    request = parse_edit_request("polish_text", arguments, config, snapshot)
    result = await Service(config, snapshot, lambda: Batch("")).polish(arguments)
    validate_final(result, request.items)
    assert [item["id"] for item in result["items"]] == list(by_id)
    assert [item["text"] for item in result["items"]] == [case["good"] for case in selected]
    assert all(item["flag"] is None and not item["regenerated"] for item in result["items"])
    assert all(isinstance(item["diagnosis"], str) and item["diagnosis"] for item in result["items"])
    assert len(calls) == result["providers"][0]["model_calls"] == (len(selected) + 3) // 4
    assert all(len(value.items) <= 4 for value in calls)
