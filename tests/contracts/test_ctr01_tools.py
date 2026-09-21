import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from copyeditor.config import load_config
from copyeditor.metrics import Metrics
from copyeditor.providers.base import GenerationResult, ProviderFailure, Usage
from copyeditor.rules import load_rules
from copyeditor.service import Service
from .harness import ProviderQueue, assert_subset, load_cases, generation_result, fixture_schema, validate_fixture
from copyeditor.requests import parse_edit_request
from copyeditor.rewrite_service import rewrite

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
    monkeypatch.setattr("copyeditor.service.Metrics", lambda start, model, pricing, **kwargs: Metrics(start, model, pricing, clock=lambda: 101, **kwargs))

    async def call(tool, arguments, responses, settings=None, *, prepared=False):
        queue = ProviderQueue(responses)
        inputs, constructed, estimates = [], [], []
        class Provider:
            async def generate(self, value):
                inputs.append(value)
                return generation_result(queue.take())
            async def estimate_input(self, value):
                estimates.append(value)
                return 0
        def factory():
            constructed.append(True)
            return Provider()
        selected_config, selected_snapshot = settings or (config, snapshot)
        if prepared and arguments.get("degree") == "rewrite":
            request = parse_edit_request(tool, arguments, selected_config, selected_snapshot)
            meter = Metrics(100, selected_config["model"], selected_config["pricing"], lambda: 101, degree="rewrite")
            payload = await rewrite(request, selected_config, selected_snapshot, factory, meter, items_route="items" in arguments)
        else:
            supplied = {k: v for k, v in arguments.items() if k != "degree"} if prepared and arguments.get("degree") == "polish" else arguments
            payload = await getattr(Service(selected_config, selected_snapshot, factory), {"polish_text": "polish", "lint_text": "lint"}[tool])(supplied)
        queue.assert_exhausted()
        assert len(inputs) == len(responses), "Provider underflow must not be hidden by service errors"
        assert len(constructed) == bool(responses)
        assert payload["model_calls"] == len(inputs) and payload["latency_ms"] == 1000
        assert estimates == (inputs if arguments.get("degree") == "rewrite" else [])
        case = dict(tool=tool, input=arguments)
        Draft202012Validator(fixture_schema(case)).validate(payload)
        validate_fixture(payload, case)
        return payload, inputs
    return call


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
async def test_ac_02_2_ac_02_3_ac_02_4_ac_02_10_ac_02_11_ac_02_12_ctr01_contract_service(case, invoke, cases_executed):
    payload, inputs = await invoke(case["tool"], case["input"], case["provider"])
    assert_subset(payload, case["expect"])
    assert payload["usage"] == dict.fromkeys(Usage._fields, None if inputs else 0)
    assert payload["cost"] is None
    if case["input"].get("degree") != "rewrite" and len(inputs) == 2:
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


@pytest.mark.asyncio
async def test_ac_07_2_ctr01_prepared_queue_counts_diagnosis_without_retry(invoke):
    from .harness import rewrite_case
    case = rewrite_case()
    payload, inputs = await invoke(case["tool"], case["input"], case["provider"], prepared=True)
    assert_subset(payload, case["expect"])
    assert [value.stage for value in inputs] == ["diagnose", "rewrite"]
    assert not payload["regenerated"] and inputs[1].diagnoses[0].diagnosis.status == "no_issue"


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [False, True])
async def test_ac_07_4_ctr01_rewrite_queue_shortage_or_surplus_is_never_hidden(invoke, extra):
    from .harness import rewrite_case
    case = rewrite_case()
    responses = case["provider"] + case["provider"][1:] if extra else case["provider"][:1]
    with pytest.raises(AssertionError):
        await invoke(case["tool"], case["input"], responses, prepared=True)


@pytest.mark.asyncio
@pytest.mark.consumer("CTR-02")
@pytest.mark.consumer("CTR-04")
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("degree", ["polish", "rewrite"])
@pytest.mark.parametrize("items_route", [False, True])
async def test_generation4_prepared_service_keeps_real_response_validation(enabled, degree, items_route):
    from .harness import invoke_generation4
    arguments = dict(language="en", degree=degree)
    originals = [dict(id="a", text="Original."), dict(id="b", text="Original.")]
    arguments.update(items=originals) if items_route else arguments.update(text="Original.")
    ids = [item["id"] for item in originals] if items_route else ["text"]
    candidate = lambda identity: dict(id=identity, text="Candidate.", flag=None, diagnosis=None if degree == "polish" else "Clearer wording.")
    case = dict(generation=4, judgment_enabled=enabled, tool="polish_text", input=arguments,
                provider=[dict(items=[candidate(identity) for identity in reversed(ids)])],
                expect=dict(status="ok", schema_version=4, degree=degree, judgment_enabled=enabled))
    payload, inputs = await invoke_generation4(case, ROOT)
    rows = payload["items"] if items_route else [payload]
    assert [row["text"] for row in rows] == ["Candidate."] * len(ids)
    assert all(row["regenerated"] is False for row in rows)
    assert [row["diagnosis"] for row in rows] == [candidate(ids[0])["diagnosis"]] * len(ids)
    if items_route:
        assert [row["id"] for row in rows] == ids
    assert all((row["detection"]["status"] == "eligible") if enabled else row["detection"] is None for row in rows)
    assert [item.id for item in inputs[0].items] == ids


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["extra", "missing", "schema", "correlation", "expected"])
async def test_generation4_fixture_rejects_queue_and_response_mismatches(fault):
    from .harness import assert_fixture, invoke_generation4
    case = dict(generation=4, judgment_enabled=False, tool="polish_text",
                input=dict(text="Original.", language="en", degree="polish"),
                provider=[dict(items=[dict(id="text", text="Candidate.", flag=None, diagnosis=None)])],
                expect=dict(status="ok", schema_version=4))
    if fault in ("extra", "missing"):
        case["provider"] = case["provider"] * (2 if fault == "extra" else 0)
        with pytest.raises(AssertionError, match="Unconsumed|underflow"):
            await invoke_generation4(case, ROOT)
        return
    payload, _ = await invoke_generation4(case, ROOT)
    if fault == "schema":
        payload["schema_version"] = 3
        from jsonschema import ValidationError
    elif fault == "correlation":
        payload["providers"][0]["model_calls"] = 0
        from copyeditor.requests import ValidationError
    else:
        case["expect"]["status"] = "error"
        ValidationError = AssertionError
    with pytest.raises(ValidationError):
        assert_fixture(payload, case)
