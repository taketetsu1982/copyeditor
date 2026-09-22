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

ROOT = Path(__file__).resolve().parents[2]
CASES = load_cases(ROOT / "contracts/tools.md", "contract-case")
pytestmark = pytest.mark.consumer("CTR-01")


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


from .harness import generation4_cases, invoke_generation4
CURRENT_CASES = generation4_cases(ROOT / "contracts/tools.md")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CURRENT_CASES, ids=lambda case: "generation4-" + case["name"])
async def test_generation4_migrated_contract_guarantees(case):
    payload, _ = await invoke_generation4(case, ROOT)
    assert payload["schema_version"] == 4
    if payload["status"] == "error":
        assert "text" not in payload and "items" not in payload and "diagnosis" not in payload


def test_generation4_migration_retains_every_historical_case():
    assert len(CURRENT_CASES) == 31
    assert {case["name"] for case in CURRENT_CASES} == {case["name"] for case in CASES}
    assert all(case["generation"] == 4 for case in CURRENT_CASES)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["editing", "judgment"])
async def test_generation4_fixture_rejects_fabricated_zero_usage(monkeypatch, role):
    from copyeditor.edit_service import EditService
    original = EditService.polish
    async def fabricated(self, arguments):
        payload = await original(self, arguments)
        next(row for row in payload["providers"] if row["role"] == role)["usage"] = dict.fromkeys(Usage._fields, 0)
        return payload
    monkeypatch.setattr(EditService, "polish", fabricated)
    case = dict(generation=4, judgment_enabled=True, tool="polish_text", input=dict(text="Original.", language="en"),
                provider=[dict(items=[dict(id="text", text="Candidate.", flag=None, diagnosis=None)])], expect=dict(status="ok"))
    with pytest.raises(AssertionError, match="Provider usage differs"):
        await invoke_generation4(case, ROOT)


@pytest.mark.asyncio
async def test_generation4_fixture_aggregates_known_and_missing_usage_components():
    responses = [GenerationResult(json.dumps(dict(items=[dict(id="text", text=text, flag=None, diagnosis=None)])), "stop", usage)
                 for text, usage in [("Pay 11.", Usage(1, None, 3)), ("Pay 10.", Usage(2, 4, 6))]]
    case = dict(generation=4, judgment_enabled=False, tool="polish_text", input=dict(text="Pay 10.", language="en"),
                provider=responses, expect=dict(status="ok", regenerated=True,
                    providers=[dict(usage=dict(input_tokens=3, output_tokens=None, total_tokens=9))]))
    await invoke_generation4(case, ROOT)
