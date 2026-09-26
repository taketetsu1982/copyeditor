import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from copyeditor.providers.vertex import Generation, ProviderFailure

SPEC = importlib.util.spec_from_file_location("copyeditor_evaluation", Path(__file__).parents[2] / "scripts/evaluate.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def synthetic_cases():
    tiers = ("\u5909\u3048\u306a\u3044", "\u30ae\u30ea\u5909\u3048\u306a\u3044", "\u30ae\u30ea\u5909\u3048\u308b", "\u5909\u3048\u308b", "AI\u81ed\u3059\u304e\u308b")
    return [{"tier": tier, "sent_text": f"Synthetic sentence {index * 3 + offset}."}
            for index, tier in enumerate(tiers) for offset in range(3)]


@pytest.mark.asyncio
@pytest.mark.parametrize("matched_indices,passed", [(set(range(15)), True), ({0, 1, 2, 9, 10, 11, 12, 13, 14}, True), ({0, 1, 2, 10, 11, 12, 13, 14}, False), (set(range(1, 15)), False)])
async def test_evaluation_threshold_and_protected_tier_are_independent(matched_indices, passed):
    cases = synthetic_cases()
    outputs = []
    for index, case in enumerate(cases):
        should_change = index >= 6
        change = should_change if index in matched_indices else not should_change
        outputs.append(Generation("Synthetic replacement." if change else case["sent_text"]))
    provider = AsyncMock()
    provider.polish.side_effect = outputs
    progress = []
    report = await evaluation.evaluate(cases, provider, progress=progress.append)
    assert report["matches"] == len(matched_indices) and report["passed"] is passed
    assert report["total"] == 15 and provider.polish.await_count == 15
    assert report["unchanged_tier_matches"] == len(matched_indices & {0, 1, 2})
    assert len(progress) == 15 and "Synthetic" not in json.dumps(progress)


@pytest.mark.asyncio
async def test_model_failure_never_counts_as_unchanged_success():
    provider = AsyncMock()
    provider.polish.side_effect = ProviderFailure(retries=2)
    report = await evaluation.evaluate(synthetic_cases(), provider)
    assert report["matches"] == report["unchanged_tier_matches"] == 0
    assert report["passed"] is False
    assert all(row["result"] == "model_error" and row["text"] is None for row in report["cases"])


@pytest.mark.parametrize("invalid", ["count", "tier", "distribution", "blank", "too_long"])
def test_invalid_external_cases_are_rejected(invalid):
    cases = synthetic_cases()
    if invalid == "count":
        cases.pop()
    elif invalid == "tier":
        cases[0]["tier"] = "unknown"
    elif invalid == "distribution":
        cases[0]["tier"] = cases[4]["tier"]
    else:
        cases[0]["sent_text"] = " " if invalid == "blank" else "x" * 12001
    with pytest.raises(ValueError):
        evaluation.validate_cases(cases)


def test_failed_atomic_write_preserves_previous_report(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    path.write_text('{"previous":true}')
    monkeypatch.setattr(evaluation.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("Synthetic failure")))
    with pytest.raises(OSError):
        evaluation.atomic_write(path, {"new": True})
    assert json.loads(path.read_text()) == {"previous": True}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.asyncio
async def test_evaluation_retains_failed_call_usage_and_retries_without_exception_text():
    usage = {"prompt_tokens": 17, "total_tokens": 23}
    provider = AsyncMock()
    provider.polish.side_effect = [ProviderFailure(retries=2, usage=usage), RuntimeError("PRIVATE_EXCEPTION")] * 7 + [ProviderFailure()]
    progress = []
    report = await evaluation.evaluate(synthetic_cases(), provider, progress=progress.append)
    assert report["matches"] == 0
    assert report["cases"][0]["usage"] == usage and report["cases"][0]["retries"] == 2
    assert report["cases"][1]["usage"] == {} and report["cases"][1]["retries"] == 0
    assert "PRIVATE_EXCEPTION" not in json.dumps(report) + json.dumps(progress)
