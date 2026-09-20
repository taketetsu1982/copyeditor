"""Polish orchestration observations with fake providers.
AC-08-2, AC-08-3: gated targets and successful unchanged/not-run originals.
AC-08-4, AC-08-5: per-item verification and final-candidate-only retries.
AC-08-7, AC-08-12, AC-08-16: no partial failure results, cancellation and HTML/items."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from copyeditor.config import load_config
from copyeditor.judged_metrics import JudgedMetrics
from copyeditor.judged_service import polish
from copyeditor.judgment import ACTION_CRITERIA
from copyeditor.judgment_config import resolve_judgment_config
from copyeditor.providers.typesafe import TypeSafe
from copyeditor.requests import parse_edit_request
from copyeditor.rules import load_rules
from copyeditor.service import Service
from test_service import Fake, batch


class Editor(Fake):
    def __init__(self, queue):
        super().__init__(queue)
        self.estimates = []

    async def estimate_input(self, value):
        self.estimates.append(value)
        return 0


async def run(arguments, queue=(), *, gates=(0.9,), verify=0.9, fail=False, cancel=False):
    base = load_config(Path("absent-config"), {"GOOGLE_CLOUD_PROJECT": "test"})
    config = {**base.values, **resolve_judgment_config({}, {}).values}
    snapshot = load_rules(Path("rules"), None)
    request = parse_edit_request("polish_text", arguments, config, snapshot)
    editor, wires = Editor(queue), []
    def handler(wire):
        data = json.loads(wire.content)
        wires.append(data)
        if cancel:
            raise asyncio.CancelledError()
        checking = "pairs" in data["state"]
        if fail and checking:
            return httpx.Response(500, content=b"PRIVATE")
        answers = {}
        for key, question in data["questions"].items():
            ordinal, predicate = key.split(".")
            if question["type"] == "choice":
                answers[key] = dict(type="choice", choice="preserve_as_is", confidence=0,
                                    probabilities={action: int(action == "preserve_as_is") for action in ACTION_CRITERIA})
            else:
                probability = verify if checking else gates[int(ordinal[1:]) - 1] if predicate == "gate" else 0.7
                answers[key] = dict(type="noul", noul=probability)
        return httpx.Response(200, json=dict(model="jev-1.13.0", answers=answers, usage=dict(input_tokens=20, output_tokens=1)))
    adapter = TypeSafe(SimpleNamespace(reveal=lambda: "synthetic"), transport=httpx.MockTransport(handler))
    metrics = JudgedMetrics(0, config["model"], {}, clock=lambda: 0)
    try:
        result = await polish(Service(config, snapshot, editor.factory), request, adapter, metrics, items_route="items" in arguments)
        return result, editor, wires
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_all_keep_has_no_editor_construction_or_preflight():
    result, editor, wires = await run(dict(text="Example."), gates=(0.52,))
    assert result["status"] == "ok" and result["text"] == "Example."
    assert result["editing"] == "not_run" and result["detection"]["status"] == "insufficient"
    assert len(wires) == 1 and editor.created == 0 and not editor.estimates


@pytest.mark.asyncio
async def test_all_blocks_detected_only_eligible_edited_and_compared_once():
    args = dict(items=[dict(id="a", text="Example."), dict(id="b", text="Keep SENTINEL.")])
    result, editor, wires = await run(args, [batch("Example.", ids=["a"])], gates=(0.53, 0.52))
    assert result["status"] == "ok"
    assert [len(wire["questions"]) for wire in wires] == [14, 4]
    assert list(wires[1]["state"]["pairs"]) == ["b0001"]
    assert len(editor.inputs) == len(editor.estimates) == 1
    generated = editor.inputs[0]
    assert [item.id for item in generated.items] == ["a"]
    assert "SENTINEL" not in repr(generated)
    assert "simplify_vocabulary" in generated.system_instruction
    assert "必要な専門用語" in generated.system_instruction
    assert all(word not in generated.system_instruction for word in ("confidence", "probabilities", "axis_fallback"))
    assert result["items"][0]["detection"]["action"]["source"] == "axis_fallback"
    assert result["items"][0]["verification"]["status"] == "pass"
    assert result["items"][1]["text"] == "Keep SENTINEL."


@pytest.mark.asyncio
@pytest.mark.parametrize("verify,status", [(0.2, "fail"), (0.5, "indeterminate")])
async def test_comparison_refusal_restores_original_without_new_generation(verify, status):
    result, editor, wires = await run(dict(text="Example."), [batch("Examples.")], verify=verify)
    assert result["status"] == "ok" and result["text"] == "Example."
    assert result["flag"]["kind"] == "verification_rejected" and result["verification"]["status"] == status
    assert result["flag"]["checks"] == ["meaning", "scope", "natural", "achieved"]
    assert len(editor.inputs) == 1 and len(wires) == 2
    assert wires[1]["state"]["pairs"]["b0001"]["candidate"] == "Examples."


@pytest.mark.asyncio
async def test_last_failure_discards_every_partial_item():
    result, editor, wires = await run(dict(text="Example."), [batch("Example.")], fail=True)
    assert result["status"] == "error" and result["error"]["code"] == "provider_error"
    assert "text" not in result and "items" not in result and "PRIVATE" not in repr(result)
    assert result["model_called"] and [row["model_calls"] for row in result["providers"]] == [1, 2]


@pytest.mark.asyncio
async def test_preservation_retry_only_verifies_final_survivor():
    result, editor, wires = await run(dict(text="Pay 10."), [batch("Pay 11."), batch("Pay 10.")])
    assert result["status"] == "ok" and result["regenerated"]
    assert len(editor.inputs) == len(editor.estimates) == 2 and len(wires) == 2
    assert wires[1]["state"]["pairs"]["b0001"]["candidate"] == "Pay 10."


@pytest.mark.asyncio
async def test_protected_html_skips_all_calls_and_cancel_propagates():
    result, editor, wires = await run(dict(text="<code>fixed</code>", format="html"))
    assert result["status"] == "ok" and result["detection"]["status"] == "not_run"
    assert not wires and not editor.estimates and not editor.created
    with pytest.raises(asyncio.CancelledError):
        await run(dict(text="Example."), cancel=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("final,code", [("<p>Pay 10.</p>", None), ("<div>Pay 10.</div>", "html_structure")])
async def test_html_retry_preserves_structure_or_discards_request(final, code):
    result, editor, wires = await run(dict(text="<p>Pay 10.</p>", format="html"),
                                     [batch("<div>Pay 10.</div>"), batch(final)])
    assert len(editor.inputs) == 2 and len(wires) == (1 if code else 2)
    assert (result["error"]["code"] if code else result["text"]) == (code or "<p>Pay 10.</p>")


@pytest.mark.asyncio
async def test_two_candidates_share_one_verification_request():
    args = dict(items=[dict(id="a", text="Example."), dict(id="b", text="Another example.")])
    result, editor, wires = await run(args, [batch("Example.", "Another example.")], gates=(0.9, 0.9))
    assert result["status"] == "ok" and len(editor.inputs) == 1
    assert [len(wire["questions"]) for wire in wires] == [14, 8]
    assert list(wires[1]["state"]["pairs"]) == ["b0001", "b0002"]
