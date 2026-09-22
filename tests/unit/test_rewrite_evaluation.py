import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import rewrite_evaluation as evaluation


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    plan = evaluation.freeze(1)
    output = tmp_path_factory.mktemp("evaluation") / "rewrite-evaluation.json"
    artifact = asyncio.run(evaluation.run(plan, output))
    assert json.loads(output.read_text()) == artifact and len(artifact["trials"]) == 120
    assert evaluation.summarize(plan, artifact)["status"] == "unreviewed"
    for trial in artifact["trials"]:
        natural = 13 <= int(trial["id"].split("-")[1]) <= 18
        trial["judgment"] = dict(a=None if natural else True, b=True, c=True,
                                d=True if natural else None, reason="Synthetic test judgment; not owner evidence.")
    return plan, artifact


def test_ac_07_9_ac_07_13_inv13_fixture_records_all_trials_without_quality_acceptance(completed):
    plan, artifact = completed
    result = evaluation.summarize(plan, artifact)
    assert result["criteria_met"] and not result["quality_accepted"]
    assert result["groups"]["acceptance"] == dict(planned=120, passed=120, rate=1)
    assert result["groups"]["regression"] == dict(planned=0, passed=0, rate=None)
    assert all(t["response"]["providers"][0]["model_calls"] == 1 and not t["error"] for t in artifact["trials"])
    assert all(e["instruction_hashes"].keys() == {"rewrite"} for e in plan["entries"])


@pytest.mark.parametrize("change", ["missing", "duplicate", "moved", "plan", "hash", "boolean"])
def test_ac_07_13_inv13_frozen_population_and_judgment_integrity(completed, change):
    plan, artifact = deepcopy(completed)
    if change == "missing": artifact["trials"].pop()
    elif change == "duplicate": artifact["trials"][-1] = artifact["trials"][0]
    elif change == "moved": artifact["trials"][0]["group"] = "regression"
    elif change == "plan": artifact["plan"]["revision"] += 1
    elif change == "hash": plan["entries"][0]["sha256"] = "0" * 64
    elif change == "boolean": artifact["trials"][0]["judgment"]["b"] = 1
    with pytest.raises(ValueError): evaluation.summarize(plan, artifact)


@pytest.mark.parametrize("change", ["meaning", "unnecessary", "natural", "all_original", "per_example", "per_repeat", "error", "flag", "unknown", "metadata"])
def test_ac_07_9_ac_07_13_inv13_quality_rates_and_must_failures_remain_visible(completed, change):
    plan, artifact = deepcopy(completed)
    trials = artifact["trials"]
    if change in ("meaning", "unnecessary"):
        trials[0]["judgment"]["b" if change == "meaning" else "c"] = False
    elif change == "natural": trials[60]["response"]["text"] += " "
    elif change == "all_original":
        cases = evaluation.fixtures()
        for trial in trials: trial["response"]["text"] = cases[trial["id"]]["bad"]
    elif change == "per_example":
        for trial in trials[:2]: trial["judgment"]["a"] = False
    elif change == "per_repeat":
        for index in (0, 5, 10, 15): trials[index]["judgment"]["a"] = False
    elif change == "error": trials[0]["response"] = None; trials[0]["error"] = "evaluation_error"
    elif change == "flag": trials[0]["response"]["flag"] = dict(kind="unfixable", reason="No edit.", checks=[])
    elif change == "metadata": trials[0]["response"]["model"] = "changed-model"
    elif change == "unknown": trials[0]["judgment"]["b"] = None
    result = evaluation.summarize(plan, artifact)
    assert not result["criteria_met"] and not result["quality_accepted"]
    assert result["groups"]["acceptance"]["planned"] == 120


def test_ac_07_13_boundary_plan_is_unmeasured_and_keeps_impossible_context_conditions():
    plan = evaluation.batch_plan()
    assert plan["status"] == "unmeasured" and plan["repeats"] == 3 and len(plan["conditions"]) == 30
    assert len(plan["variants"]) == 2 and len(plan["failures"]) == 5
    assert sum(not c["valid"] for c in plan["conditions"]) == 3


def test_ac_07_13_plan_rejects_mode_and_revision():
    for revision, mode in ((0, "fixture"), (True, "fixture"), (1, "implicit-live")):
        with pytest.raises(ValueError): evaluation.freeze(revision, mode)


def test_ac_07_13_regressions_never_change_acceptance_denominators(completed, tmp_path, monkeypatch):
    import shutil
    plan, artifact = deepcopy(completed)
    cases = evaluation.fixtures()
    cases["rewrite-extra"] = dict(cases["rewrite-01"], id="rewrite-extra")
    (tmp_path / "src/copyeditor").mkdir(parents=True)
    shutil.copy(evaluation.ROOT / "src/copyeditor/prompt.py", tmp_path / "src/copyeditor/prompt.py")
    shutil.copytree(evaluation.ROOT / "examples", tmp_path / "examples")
    (tmp_path / "examples/ja/rewrite-extra.yaml").write_text("Synthetic extra fixture")
    monkeypatch.setattr(evaluation, "ROOT", tmp_path)
    monkeypatch.setattr(evaluation, "fixtures", lambda: cases)
    monkeypatch.setattr(evaluation.subprocess, "check_output", lambda *a, **k: plan["source_commit"])
    frozen = evaluation.freeze(1)
    artifact["plan"] = deepcopy(frozen)
    for repeat in range(1, 6):
        artifact["trials"].append(dict(deepcopy(artifact["trials"][0]), group="regression", id="rewrite-extra", repeat=repeat))
    assert evaluation.summarize(frozen, artifact)["groups"]["acceptance"]["planned"] == 120
    artifact["trials"][-1]["judgment"]["b"] = False
    result = evaluation.summarize(frozen, artifact)
    assert not result["criteria_met"] and result["must_failures"] == 1


@pytest.mark.asyncio
async def test_interruption_preserves_unexecuted_regression_trials_and_denominator(tmp_path, monkeypatch):
    path = tmp_path / 'interrupted.json'
    async def cancel(*args):
        checkpoint = json.loads(path.read_text())
        assert len(checkpoint['trials']) == 120
        assert all(t['error'] == 'not_run' for t in checkpoint['trials'])
        raise asyncio.CancelledError()
    monkeypatch.setattr(evaluation, 'call_api', cancel)
    plan = evaluation.freeze(2)
    with pytest.raises(asyncio.CancelledError):
        await evaluation.run(plan, path)
    artifact = json.loads(path.read_text())
    assert len(artifact['trials']) == 120 and artifact['trials'][0]['error'] == 'evaluation_error'
    result = evaluation.summarize(plan, artifact)
    assert result['not_run'] == 119 and result['groups']['acceptance']['planned'] == 120
    assert not result['criteria_met'] and not result['quality_accepted']
