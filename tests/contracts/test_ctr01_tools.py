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

    async def call(tool, arguments, responses):
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
        payload = await getattr(Service(config, snapshot, factory), {"polish_text": "polish", "lint_text": "lint"}[tool])(arguments)
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
