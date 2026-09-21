"""Shared polish retry, final-candidate retention and atomic failure with fake transports."""
import asyncio
import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from copyeditor.edit_pipeline import polish
from copyeditor.judged_metrics import EditMetrics
from copyeditor.judgment_v2 import POLICY_ID, snapshot
from copyeditor.providers.base import Background, GenerationResult, SourceItem, Usage
from copyeditor.providers.typesafe import TypeSafe
from copyeditor.requests import Request
from copyeditor.rules import load_rules

REGISTRY = partial(snapshot, thresholds={"synthetic": dict(id="synthetic", floor=0.5, gap=0.2, meaning_floor=0.8)},
                   pairs={(POLICY_ID, "synthetic")})


def candidate(text="Candidate.", identity="a", flag=None):
    return dict(id=identity, text=text, flag=flag, diagnosis=None)


async def run(queue, *, enabled=True, texts=("Original.",), gates=(0.9,), verify=0.3, meaning=0.9,
              format="text", fail=False, cancel=False):
    inputs, wires, estimates, created = [], [], [], []
    async def estimate(value):
        estimates.append(value)
        return 0
    async def generate(value):
        inputs.append(value)
        if cancel: raise asyncio.CancelledError()
        response = queue[len(inputs) - 1]
        raw = response if isinstance(response, str) else json.dumps(dict(items=response))
        return GenerationResult(raw, "stop", Usage(0, 0, 0))
    def factory():
        created.append(True)
        return SimpleNamespace(estimate_input=estimate, generate=generate)
    def handler(wire):
        payload = json.loads(wire.content)
        wires.append(payload)
        checking = "originals" in payload["state"]
        if checking and fail: return httpx.Response(500)
        answers = {key: dict(type="Noul", noul=(meaning if key.endswith("meaning") else verify) if checking
                            else gates[int(key.split(".")[0][1:]) - 1]) for key in payload["questions"]}
        return httpx.Response(200, json=dict(model="jev-1.13.0", answers=answers))
    adapter = TypeSafe(SimpleNamespace(reveal=lambda: "synthetic"), transport=httpx.MockTransport(handler))
    config = {"judgment." + key: value for key, value in dict(enabled=enabled, policy_version=POLICY_ID,
        thresholds_version="synthetic", polish_deadline_ms=120000, rewrite_deadline_ms=240000,
        timeout_ms=10000, max_calls=64, input_budget=262144).items()}
    config.update({"length_ratio.min": 0.5, "length_ratio.max": 2})
    request = Request(tuple(SourceItem(chr(97 + i), text, "untrusted context") for i, text in enumerate(texts)),
                      "en", format, Background("audience", "purpose", "same tone", "message"))
    metrics = EditMetrics(0, "editor", {}, clock=lambda: 0, judgment_enabled=enabled)
    service = SimpleNamespace(config=config, snapshot=load_rules(Path("rules"), None), provider_factory=factory)
    try:
        result = await polish(service, request, adapter, metrics, items_route=len(texts) > 1, registry=REGISTRY)
        return result, inputs, wires, estimates, created
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_off_and_on_success_keep_candidate_and_use_count_preflight(enabled):
    result, inputs, wires, estimates, _ = await run([[candidate()]], enabled=enabled)
    assert result["status"] == "ok" and result["text"] == "Candidate."
    assert result["diagnosis"] is None and result["flag"] is None
    assert len(inputs) == len(estimates) == 1 and len(wires) == (2 if enabled else 0)
    assert len(result["providers"]) == (2 if enabled else 1)
    assert "verification" not in result and inputs[0].background.tone == "same tone"


@pytest.mark.asyncio
@pytest.mark.parametrize("first,second", [("Original.", "Original."), ("Original.", "Candidate."), ("Candidate.", "Alternative.")])
@pytest.mark.parametrize("verify,meaning", [(0.8, 0.9), (0.3, 0.7)])
async def test_one_shared_retry_returns_second_candidate_even_without_verification_pass(first, second, verify, meaning):
    result, inputs, wires, _, _ = await run([[candidate(first)], [candidate(second)]], verify=verify, meaning=meaning)
    assert result["status"] == "ok" and result["text"] == second and result["regenerated"]
    assert result["flag"] is None and len(inputs) == 2
    assert sum("originals" not in wire["state"] for wire in wires) == 1
    assert len(wires) == 1 + sum(text != "Original." for text in (first, second))
    assert inputs[0].background == inputs[1].background


@pytest.mark.asyncio
async def test_preservation_and_verification_share_retry_and_keep_rejected_body():
    result, inputs, wires, _, _ = await run([[candidate("Pay 11.")], [candidate("Pay 12.")]],
                                          texts=("Pay 10.",), verify=0.8)
    assert result["status"] == "ok" and result["text"] == "Pay 12."
    assert result["flag"]["kind"] == "rejected" and "numbers" in result["flag"]["checks"]
    assert len(inputs) == 2 and len(wires) == 3


@pytest.mark.asyncio
async def test_detection_subset_keeps_original_ordinals_and_unchanged_excluded_item():
    result, inputs, wires, _, _ = await run([[candidate(identity="b")]], texts=("Excluded.", "Original."), gates=(0.1, 0.9))
    assert result["status"] == "ok" and result["items"][0]["text"] == "Excluded."
    assert result["items"][0]["diagnosis"] is None
    assert [item.id for item in inputs[0].items] == ["b"]
    assert list(wires[1]["state"]["texts"]) == ["b0002"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text,format,gates,count", [("Original.", "text", (0.1,), 1), ("<code>x</code>", "html", (0.9,), 0)])
async def test_nonadmitted_items_never_construct_editor(text, format, gates, count):
    result, inputs, wires, estimates, created = await run([], texts=(text,), format=format, gates=gates)
    assert result["status"] == "ok" and result["text"] == text
    assert not inputs and not estimates and not created and len(wires) == count


@pytest.mark.asyncio
async def test_unfixable_skips_retry_and_verification():
    result, inputs, wires, _, _ = await run([[candidate("Original.", flag=dict(kind="unfixable", reason="Ambiguous."))]])
    assert result["status"] == "ok" and result["flag"]["kind"] == "unfixable"
    assert len(inputs) == len(wires) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("queue,fail,code", [(["{}"], False, "invalid_response"), ([[candidate("Original.")], "{}"], False, "invalid_response"),
    ([[candidate()]], True, "provider_error")])
async def test_malformed_generation_or_failed_verification_discards_all_candidates(queue, fail, code):
    result, inputs, _, _, _ = await run(queue, fail=fail)
    assert result["status"] == "error" and result["error"]["code"] == code
    assert "text" not in result and "items" not in result and len(inputs) == len(queue)


@pytest.mark.asyncio
async def test_cancel_propagates_without_a_retry():
    with pytest.raises(asyncio.CancelledError):
        await run([], cancel=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_invalid_html_stops_before_any_provider(enabled):
    result, inputs, wires, estimates, created = await run([], enabled=enabled, texts=("<p",), format="html")
    assert result["error"]["code"] == "invalid_input" and not (inputs or wires or estimates or created)
