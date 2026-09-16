import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from copyeditor.config import load_config
from copyeditor.metrics import Metrics
from copyeditor.providers.base import GenerationResult, ProviderFailure, Usage
from copyeditor.responses import output_schema, validate_final
from copyeditor.rules import load_rules
from copyeditor.service import Service
from .harness import ProviderQueue, assert_subset, load_cases

ROOT = Path(__file__).resolve().parents[2]
CASES = load_cases(ROOT / "contracts/tools.md", "contract-case")
pytestmark = pytest.mark.consumer("CTR-01")


@pytest.fixture(scope="module")
def cases_executed():
    names = [case["name"] for case in CASES]
    assert names and len(names) == len(set(names))
    completed = []
    yield completed
    assert sorted(completed) == sorted(names), "Every contract case must execute successfully"


@pytest.fixture
def invoke(monkeypatch):
    config = load_config(ROOT / "absent-config", {"GOOGLE_CLOUD_PROJECT": "test", "COPYEDITOR_DEFAULT_LANGUAGE": "en",
        "COPYEDITOR_MODEL": "test-model", "COPYEDITOR_PRICING": json.dumps({"test-model": {
            "currency": "USD", "input_per_million": 2, "output_per_million": 4}})})
    snapshot = load_rules(ROOT / "rules", None)
    monkeypatch.setattr("copyeditor.service.monotonic", lambda: 100)
    monkeypatch.setattr("copyeditor.service.Metrics", lambda start, model, pricing: Metrics(start, model, pricing, clock=lambda: 101))

    async def call(tool, arguments, responses, settings=None):
        queue = ProviderQueue(responses)
        inputs, constructed = [], []
        class Provider:
            async def generate(self, value):
                inputs.append(value)
                response = queue.take()
                if isinstance(response, (GenerationResult, ProviderFailure)):
                    return response
                return GenerationResult(json.dumps({k: v for k, v in response.items() if k != "finish"}),
                                        response.get("finish", "stop"), Usage(None, None, None))
        def factory():
            constructed.append(True)
            return Provider()
        selected_config, selected_snapshot = settings or (config, snapshot)
        payload = await getattr(Service(selected_config, selected_snapshot, factory), {"polish_text": "polish", "lint_text": "lint"}[tool])(arguments)
        queue.assert_exhausted()
        assert len(inputs) == len(responses), "Provider underflow must not be hidden by service errors"
        assert len(constructed) == bool(responses)
        assert payload["model_calls"] == len(inputs) and payload["latency_ms"] == 1000
        Draft202012Validator(output_schema(tool)).validate(payload)
        validate_final(payload)
        return payload, inputs
    return call


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
async def test_ac_02_2_ac_02_3_ac_02_4_ac_02_10_ac_02_11_ac_02_12_ctr01_contract_service(case, invoke, cases_executed):
    payload, inputs = await invoke(case["tool"], case["input"], case["provider"])
    assert_subset(payload, case["expect"])
    assert payload["usage"] == dict.fromkeys(Usage._fields, None if inputs else 0)
    assert payload["cost"] is None
    if len(inputs) == 2:
        first, retry = inputs
        assert all(item in first.items for item in retry.items)
        assert retry[1:] == first[1:]
    if "items" in payload:
        assert [item["id"] for item in payload["items"]] == [item["id"] for item in case["input"]["items"]]
    cases_executed.append(case["name"])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_ac_02_3_ctr01_known_usage_and_failure_cost(invoke, failure):
    response = ProviderFailure("provider_timeout", Usage(10, 20, 35)) if failure else GenerationResult(
        json.dumps({"items": [{"id": "text", "text": "Hello.", "flag": None}]}), "stop", Usage(10, 20, 35))
    payload, _ = await invoke("polish_text", {"text": "Hello."}, [response])
    assert payload["usage"] == dict(input_tokens=10, output_tokens=20, total_tokens=35)
    assert payload["cost"] == dict(amount="0.000100", currency="USD")
    assert payload["status"] == ("error" if failure else "ok")
    if failure: assert payload["error"]["code"] == "provider_timeout"


@pytest.mark.asyncio
async def test_ac_02_3_ctr01_retry_usage_unknown_component(invoke):
    first = GenerationResult(json.dumps({"items": [{"id": "text", "text": "Pay 11.", "flag": None}]}), "stop", Usage(10, 20, 35))
    second = ProviderFailure("provider_error", Usage(5, None, 8))
    payload, _ = await invoke("polish_text", {"text": "Pay 10."}, [first, second])
    assert payload["error"]["code"] == "provider_error" and payload["regeneration_attempted"]
    assert payload["usage"] == dict(input_tokens=15, output_tokens=None, total_tokens=43)
    assert payload["cost"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("texts", [["Hello.", "Hello."], ["Pay 11."]])
async def test_ctr01_queue_mismatch_is_not_swallowed(invoke, texts):
    responses = [{"items": [dict(id="text", text=text, flag=None)]} for text in texts]
    with pytest.raises(AssertionError):
        await invoke("polish_text", {"text": "Hello." if len(texts) == 2 else "Pay 10."}, responses)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [None, "config", "rules"])
async def test_ac_02_11_ctr01_config_and_rules_terms_reach_service(invoke, tmp_path, missing):
    base = tmp_path / "rules"; base.mkdir()
    (base / "common.md").write_bytes((ROOT / "rules/common.md").read_bytes())
    source = (ROOT / "rules/en.md").read_text()
    assert source.count('"protected_terms": []') == 1
    (base / "en.md").write_text(source.replace('"protected_terms": []', '"protected_terms": ["BetaRules"]'))
    config = load_config(tmp_path / "absent", {"GOOGLE_CLOUD_PROJECT": "test", "COPYEDITOR_DEFAULT_LANGUAGE": "en",
        "COPYEDITOR_PROTECTED_TERMS": '["AlphaConfig"]'})
    snapshot = load_rules(base, None, config_terms=config["protected_terms"])
    args = dict(items=[dict(id="a", text="AlphaConfig BetaRules remains.", context="First context"),
                       dict(id="b", text="BetaRules stays.", context="Second context")], language="en", format="markdown",
                audience="Readers", purpose="Explain", tone="Neutral", message="Keep facts")
    candidate = {None: args["items"][0]["text"], "config": "BetaRules remains.", "rules": "AlphaConfig remains."}[missing]
    first = {"items": [dict(id="a", text=candidate, flag=None), dict(id="b", text=args["items"][1]["text"], flag=None)]}
    responses = [first] + ([{"items": [first["items"][0]]}] if missing else [])
    payload, inputs = await invoke("polish_text", args, responses, settings=(config, snapshot))
    assert payload["status"] == "ok" and payload["protected_terms_checked"] == 2
    assert [item["protected_terms"] for item in payload["items"]] == [["AlphaConfig", "BetaRules"], ["BetaRules"]]
    assert [item["text"] for item in payload["items"]] == [item["text"] for item in args["items"]]
    assert [item["regenerated"] for item in payload["items"]] == [missing is not None, False]
    if missing:
        assert payload["items"][0]["flag"]["kind"] == "rejected"
        assert payload["items"][0]["flag"]["checks"] == ["protected_terms"]
        assert {item.id for item in inputs[1].items} == {"a"}
    else: assert all(item["flag"] is None for item in payload["items"])
    for index, generated in enumerate(inputs):
        assert [item._asdict() for item in generated.items] == (args["items"] if index == 0 else [args["items"][0]])
        assert generated.background._asdict() == {key: args[key] for key in ("audience", "purpose", "tone", "message")}
        assert generated.language == args["language"] and generated.format == args["format"]
        assert generated.system_instruction == inputs[0].system_instruction
