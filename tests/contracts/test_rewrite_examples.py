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
    ("rewrite-02", "number", "rejected"), ("rewrite-03", "html", "html_structure"),
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
    if code == "html_structure":
        assert result["error"]["code"] == code and "diagnosis" not in result and "text" not in result
    elif code == "rejected":
        assert result["flag"]["kind"] == code and result["flag"]["checks"] == ["numbers"]
        assert result["text"] == case["bad"]
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
    assert result["error"]["code"] == "invalid_response" and result["model_calls"] == 2
    assert "text" not in result and "diagnosis" not in result
    assert not get_assert(response["output"], {"vars": case})
