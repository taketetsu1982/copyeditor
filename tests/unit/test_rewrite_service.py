import json
from pathlib import Path

import pytest

from copyeditor.config import load_config
from copyeditor.lint import lint_response
from copyeditor.metrics import Metrics
from copyeditor.providers.base import GenerationResult, Usage
from copyeditor.requests import parse_edit_request
from copyeditor.rewrite_response import validate_rewrite_final
from copyeditor.rewrite_service import rewrite
from copyeditor.rules import load_rules


class Fake:
    def __init__(self, change=None, no_issue=False):
        self.inputs, self.estimates = [], []
        self.change, self.no_issue = change, no_issue

    def factory(self):
        return self

    async def estimate_input(self, data):
        self.estimates.append(data)
        return 100

    async def generate(self, data):
        self.inputs.append(data)
        if data.stage == "diagnose":
            body = {"diagnoses": [dict(id=item.id, status="no_issue" if self.no_issue else "issue",
                expression=None if self.no_issue else item.text, reason=None if self.no_issue else "Expression issue.")
                for item in reversed(data.items)]}
        else:
            body = {"items": [dict(id=item.id, text=item.text, flag=None) for item in reversed(data.items)]}
        if self.change:
            self.change(data, body, len(self.inputs))
        return GenerationResult(json.dumps(body), "stop", Usage(10, 20, 30))


@pytest.fixture
def setup():
    return (load_config(Path("absent-config"), {"GOOGLE_CLOUD_PROJECT": "fixture"}), load_rules(Path("rules"), None))


async def run(setup, args, fake):
    config, snapshot = setup
    request = parse_edit_request("polish_text", dict(degree="rewrite", **args), config, snapshot)
    meter = Metrics(0, config["model"], config["pricing"], lambda: 0, degree="rewrite")
    result = await rewrite(request, config, snapshot, fake.factory, meter, items_route="items" in args)
    validate_rewrite_final(result, request.items)
    assert fake.estimates == fake.inputs
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 4, 5, 32])
@pytest.mark.parametrize("language", ["ja", "en", "zh"])
@pytest.mark.parametrize("no_issue", [False, True])
async def test_ac_07_2_ac_07_3_ac_07_6_ctr01_ordered_four_item_batches(setup, count, language, no_issue):
    args = dict(items=[dict(id=f"i{i}", text="Pay 10.", context=f"note{i}") for i in range(count)],
                language=language, audience="reader")
    fake = Fake(no_issue=no_issue)
    result = await run(setup, args, fake)
    assert result["status"] == "ok" and result["language"] == language
    assert [item["id"] for item in result["items"]] == [item["id"] for item in args["items"]]
    assert all(item["text"] == "Pay 10." and not item["regenerated"] for item in result["items"])
    assert result["model_calls"] == 1 + (count + 3) // 4
    assert result["usage"]["total_tokens"] == result["model_calls"] * 30
    first, *batches = fake.inputs
    assert first.stage == "diagnose" and len(first.items) == count
    assert [len(value.items) for value in batches] == [min(4, count - i) for i in range(0, count, 4)]
    for value in batches:
        assert value.stage == "rewrite" and value.language == language and value.background == first.background
        assert [item.id for item in value.diagnoses] == [item.id for item in value.items]
        assert setup[1].languages[language].prose in value.system_instruction
    for item in result["items"]:
        assert item["diagnosis"]["status"] == ("no_issue" if no_issue else "issue")
        assert item["findings"] == lint_response(item["text"], setup[1].languages[language])["findings"]


@pytest.mark.asyncio
async def test_ac_07_4_ac_07_12_ctr02_retry_original_subset_with_frozen_diagnoses(setup):
    def change(data, body, call):
        if data.stage == "rewrite":
            for item in body["items"]:
                if item["id"] == "i1": item["text"] = "Pay 11."
    fake = Fake(change)
    result = await run(setup, {"items": [dict(id=f"i{i}", text="Pay 10.") for i in range(5)]}, fake)
    diagnosis, initial, retry, last = fake.inputs
    assert len(diagnosis.items) == 5 and len(last.items) == 1
    assert retry.items == (initial.items[1],) and retry.diagnoses == (initial.diagnoses[1],)
    assert retry.background == initial.background and retry.system_instruction == initial.system_instruction
    assert result["items"][1]["flag"]["checks"] == ["numbers"]
    assert result["items"][1]["text"] == "Pay 10."
    assert [item["regenerated"] for item in result["items"]] == [False, True, False, False, False]
    assert result["model_calls"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("initial,retry,code", [
    ("<p>Pay 11.</p>", "<div>Pay 10.</div>", "html_structure"),
    ("<div>Pay 10.</div>", "<p>Pay 11.</p>", None),
    ("<div>Pay 11.</div>", "<p>Pay 10.</p>", None)])
async def test_ac_07_12_ctr02_html_retry_keeps_existing_failure_priority(setup, initial, retry, code):
    def change(data, body, call):
        if data.stage == "rewrite": body["items"][0]["text"] = initial if call == 2 else retry
    fake = Fake(change)
    original = "<p>Pay 10.</p>"
    result = await run(setup, dict(text=original, format="html", language="en"), fake)
    assert len(fake.inputs) == 3 and fake.inputs[1] == fake.inputs[2]
    if code:
        assert result["error"]["code"] == code and "diagnosis" not in result and "text" not in result
    else:
        assert result["text"] == original and result["regenerated"]
        assert bool(result["flag"]) == ("11" in retry)


@pytest.mark.asyncio
@pytest.mark.parametrize("unfixable", [False, True])
async def test_ac_07_3_ctr02_candidate_adoption_and_unfixable_use_final_lint(setup, unfixable):
    def change(data, body, call):
        if data.stage == "rewrite":
            body["items"][0].update(text="Pay 99." if unfixable else "Pay 10!",
                flag=dict(kind="unfixable", reason="Cannot preserve meaning.") if unfixable else None)
    fake = Fake(change)
    result = await run(setup, dict(text="Pay 10.", language="en"), fake)
    assert result["text"] == ("Pay 10." if unfixable else "Pay 10!") and not result["regenerated"]
    assert result["flag"] == (dict(kind="unfixable", reason="Cannot preserve meaning.", checks=[]) if unfixable else None)
    assert result["findings"] == lint_response(result["text"], setup[1].languages["en"])["findings"]
    assert len(fake.inputs) == 2 and "items" not in result and "id" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("break_id", [False, True])
async def test_ac_07_4_ctr01_later_invalid_batch_discards_all_previous_content(setup, break_id):
    def change(data, body, call):
        if call == 3:
            body["items"][0]["id" if break_id else "text"] = "changed"
    fake = Fake(change, no_issue=True)
    result = await run(setup, {"items": [dict(id=f"i{i}", text="Pay 10.") for i in range(5)]}, fake)
    assert result["error"]["code"] == "invalid_response" and result["model_calls"] == 3
    assert not {"items", "text", "diagnosis"} & result.keys()
    assert not result["regeneration_attempted"] and result["usage"]["total_tokens"] == 90
