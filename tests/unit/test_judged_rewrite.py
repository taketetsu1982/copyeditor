import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from copyeditor.config import load_config
from copyeditor.judged_metrics import JudgedMetrics
from copyeditor.judged_service import rewrite
from copyeditor.judgment import ACTION_CRITERIA, _registry
from copyeditor.judgment_config import resolve_judgment_config
from copyeditor.providers.typesafe import TypeSafe
from copyeditor.requests import parse_edit_request
from copyeditor.rules import load_rules
from copyeditor.service import Service
from test_rewrite_service import Fake


async def run(args, editor, *, keep=(), terms=(), verify=0.9):
    config = {**load_config(Path("absent-config"), {"GOOGLE_CLOUD_PROJECT": "test"}).values,
              **resolve_judgment_config({}, {}).values}
    snapshot = load_rules(Path("rules"), None)
    snapshot = snapshot._replace(languages={key: value._replace(protected_terms=terms) for key, value in snapshot.languages.items()})
    request = parse_edit_request("polish_text", dict(degree="rewrite", **args), config, snapshot)
    wires = []
    def handler(wire):
        data = json.loads(wire.content)
        wires.append(data)
        checking, answers = "pairs" in data["state"], {}
        for key, question in data["questions"].items():
            ordinal, predicate = key.split(".")
            if question["type"] == "choice":
                answers[key] = dict(type="choice", choice="make_more_specific", confidence=0,
                    probabilities={action: int(action == "make_more_specific") for action in ACTION_CRITERIA})
            else:
                answers[key] = dict(type="noul", noul=verify if checking else 0.1 if predicate == "gate" and int(ordinal[1:]) in keep else 0.9)
        return httpx.Response(200, json=dict(model="jev-1.13.0", answers=answers, usage=dict(input_tokens=20, output_tokens=1)))
    adapter = TypeSafe(SimpleNamespace(reveal=lambda: "synthetic"), transport=httpx.MockTransport(handler))
    metrics = JudgedMetrics(0, config["model"], {}, clock=lambda: 0, degree="rewrite")
    try:
        result = await rewrite(Service(config, snapshot, editor.factory), request, adapter, metrics, items_route="items" in args)
        assert editor.estimates == editor.inputs
        return result, wires
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [5, 32])
@pytest.mark.parametrize("no_issue", [False, True])
async def test_diagnose_once_then_only_issue_batches_of_four(count, no_issue):
    editor = Fake(no_issue=no_issue)
    args = dict(items=[dict(id=f"i{i}", text="Pay 10.") for i in range(count)])
    result, wires = await run(args, editor)
    assert [len(data.items) for data in editor.inputs] == [count] + ([] if no_issue else [min(4, count - i) for i in range(0, count, 4)])
    if count == 32 and not no_issue:
        assert result["error"]["code"] == "request_budget" and "items" not in result
        assert all("pairs" not in wire["state"] for wire in wires)
        return
    assert result["status"] == "ok"
    assert [item["id"] for item in result["items"]] == [item["id"] for item in args["items"]]
    assert all(item["editing"] == ("diagnosed_no_issue" if no_issue else "generated") for item in result["items"])
    assert all(item["diagnosis"]["status"] == ("no_issue" if no_issue else "issue") for item in result["items"])
    assert any("pairs" in wire["state"] for wire in wires) == (not no_issue)


@pytest.mark.asyncio
async def test_eligible_diagnosis_and_issue_retry_keep_action_and_subset_terms():
    def change(data, body, call):
        for item in body.get("diagnoses", []):
            if item["id"] == "i1": item.update(status="no_issue", expression=None, reason=None)
        if call == 2: body["items"][0]["text"] = "Pay 11."
    editor = Fake(change)
    result, wires = await run(dict(items=[dict(id="i0", text="Pay 10."), dict(id="i1", text="Keep NAMED."),
        dict(id="i2", text="Keep SENTINEL.")]), editor, keep=(3,), terms=("NAMED", "SENTINEL"))
    assert result["status"] == "ok"
    diagnosis, initial, retry = editor.inputs
    assert [item.id for item in diagnosis.items] == ["i0", "i1"]
    assert initial.items == retry.items == (diagnosis.items[0],)
    assert initial.diagnoses == retry.diagnoses
    assert initial.system_instruction == retry.system_instruction
    assert 'Protected terms: ["NAMED"]' in diagnosis.system_instruction
    assert 'Protected terms: []' in initial.system_instruction
    assert all("SENTINEL" not in repr(data) for data in editor.inputs)
    assert "one exact expression" in diagnosis.system_instruction and "one sentence" in diagnosis.system_instruction
    assert "no_issue" in diagnosis.system_instruction and "never permits inventing information" in diagnosis.system_instruction
    for data in editor.inputs:
        assert "make_more_specific" in data.system_instruction and _registry("reference-gate-action-v1", "gate-floor-v1")[0]["action_instructions"]["make_more_specific"] in data.system_instruction
        assert all(word not in data.system_instruction for word in ("probabilities", "confidence", "axis_fallback"))
    assert [item["editing"] for item in result["items"]] == ["generated", "diagnosed_no_issue", "not_run"]
    assert [item["regenerated"] for item in result["items"]] == [True, False, False]
    assert list(wires[1]["state"]["pairs"]) == ["b0001"]
    assert "make_more_specific" in json.dumps(wires[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["missing", "duplicate", "expression"])
async def test_invalid_diagnostic_subset_fails_before_any_candidate(defect):
    def change(data, body, call):
        if defect == "missing": body["diagnoses"].pop()
        elif defect == "duplicate": body["diagnoses"].append(body["diagnoses"][0])
        else: body["diagnoses"][0]["expression"] = "Absent"
    editor = Fake(change)
    result, wires = await run(dict(items=[dict(id=f"i{i}", text="Pay 10.") for i in range(5)]), editor)
    assert result["status"] == "error" and result["error"]["code"] == "invalid_response"
    assert "items" not in result and len(editor.inputs) == len(wires) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("final,code", [("<p>Pay 10.</p>", None), ("<div>Pay 10.</div>", "html_structure")])
async def test_html_uses_one_shared_retry_and_verifies_only_final_candidate(final, code):
    def change(data, body, call):
        if data.stage == "rewrite": body["items"][0]["text"] = "<div>Pay 11.</div>" if call == 2 else final
    editor = Fake(change)
    result, wires = await run(dict(text="<p>Pay 10.</p>", format="html"), editor)
    assert len(editor.inputs) == 3 and len(wires) == (1 if code else 2)
    assert (result["error"]["code"] if code else result["text"]) == (code or final)
    if code: assert "text" not in result
    else: assert result["regenerated"] and result["verification"]["status"] == "pass"


@pytest.mark.asyncio
@pytest.mark.parametrize("text,format,keep", [("Pay 10.", "text", (1,)), ("<code>fixed</code>", "html", ())])
async def test_exempt_originals_have_no_diagnosis_or_editor_calls(text, format, keep):
    editor = Fake()
    result, wires = await run(dict(text=text, format=format), editor, keep=keep)
    assert result["status"] == "ok" and result["text"] == text
    assert result["diagnosis"] is None and result["editing"] == "not_run" and not editor.inputs


@pytest.mark.asyncio
async def test_last_batch_failure_discards_previous_candidates():
    def change(data, body, call):
        if call == 3: body["items"] = []
    editor = Fake(change)
    result, wires = await run(dict(items=[dict(id=f"i{i}", text="Pay 10.") for i in range(5)]), editor)
    assert result["error"]["code"] == "invalid_response" and "items" not in result
    assert len(editor.inputs) == 3 and len(wires) == 1


@pytest.mark.asyncio
async def test_verification_rejection_retains_issue_without_new_generation():
    editor = Fake()
    result, wires = await run(dict(text="Pay 10."), editor, verify=0.2)
    assert result["status"] == "ok" and result["flag"]["kind"] == "verification_rejected"
    assert result["diagnosis"]["status"] == "issue" and result["text"] == "Pay 10."
    assert len(editor.inputs) == len(wires) == 2


@pytest.mark.asyncio
async def test_thirty_two_targets_with_small_issue_subset_complete_in_original_order():
    def change(data, body, call):
        for item in body.get("diagnoses", []):
            if item["id"] != "i31": item.update(status="no_issue", expression=None, reason=None)
    editor = Fake(change)
    result, wires = await run(dict(items=[dict(id=f"i{i}", text="Pay 10.") for i in range(32)]), editor)
    assert result["status"] == "ok" and len(result["items"]) == 32
    assert [len(data.items) for data in editor.inputs] == [32, 1]
    assert result["items"][-1]["id"] == "i31" and result["items"][-1]["editing"] == "generated"
    assert list(wires[-1]["state"]["pairs"]) == ["b0032"]
