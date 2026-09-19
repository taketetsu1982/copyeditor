"""Automated consumer evidence, not live/native quality or client acceptance."""
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from tests.conftest import PHASE2_MODULES, REWRITE_MODULES, REWRITE_TOOL_CASES, Phase1Contracts, phase1_inventory
from tests.contracts.harness import load_cases
from tests.integration.test_phase1 import collected_contracts, run, suite

ROOT = Path(__file__).resolve().parents[2]


def test_ac_07_1_ac_07_8_ac_07_9_ac_07_13_ctr01_ctr05_all_fixed_assets_and_consumers(collected_contracts):
    cases = load_cases(ROOT / "contracts/tools.md", "contract-case")
    assert len(cases) == 31 and {c["name"] for c in cases} == REWRITE_TOOL_CASES
    modules = {item.nodeid.split("::")[0] for item in collected_contracts.items}
    assert REWRITE_MODULES <= PHASE2_MODULES <= modules
    assert {"tests/integration/test_plugin_claude.py", "tests/integration/test_plugin_codex.py",
            "tests/integration/test_plugin_distribution.py"} <= modules
    entries = phase1_inventory(ROOT)
    examples = next(values for name, values in entries.items() if "real_examples_accepted" in name)
    assert {f"ja/rewrite-{i:02}" for i in range(1, 25)} <= examples.keys()


@pytest.mark.parametrize("module", sorted(REWRITE_MODULES))
def test_ac_07_1_ac_07_3_ac_07_4_ac_07_6_ac_07_7_ac_07_11_ac_07_12_missing_consumer_fails(collected_contracts, module):
    remaining = [item for item in collected_contracts.items if item.nodeid.split("::")[0] != module]
    assert len(remaining) < len(collected_contracts.items)
    gate = Phase1Contracts(collected_contracts.config)
    gate.pytest_collection_finish(SimpleNamespace(items=remaining))
    assert f"Missing Phase 2 module: {module}" in gate.errors


@pytest.mark.parametrize("case", sorted(c for c in REWRITE_TOOL_CASES if c.startswith("rewrite_")))
def test_ac_07_4_ac_07_12_ctr01_deleted_diagnostic_case_fails_inventory(tmp_path, case):
    text = (ROOT / "contracts/tools.md").read_text()
    changed = re.sub(r"```json contract-case\n(.*?)\n```", lambda match:
        "" if json.loads(match[1])["name"] == case else match[0], text, flags=re.S)
    assert changed != text
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts/tools.md").write_text(changed)
    with pytest.raises(ValueError, match="Missing or replaced tool contract cases"):
        phase1_inventory(tmp_path)


@pytest.mark.parametrize("outcome", ["ok", "skip", "xfail"])
def test_ac_07_9_ac_01_1_ac_01_14_strict_execution_never_claims_owner_acceptance(suite, outcome):
    path = suite / "tests/integration/test_rewrite_skill.py"
    action = "pass" if outcome == "ok" else f"pytest.{outcome}('Synthetic outcome')"
    path.write_text(f"import pytest\ndef test_present():\n    {action}\n")
    result = run(suite, "tests")
    assert result.returncode == (0 if outcome == "ok" else 1), result.stdout + result.stderr
    assert ("CONTRACTS PASS" if outcome == "ok" else "CONTRACTS FAIL") in result.stdout
    assert "US-07 quality not evaluated" in result.stdout
    assert "ACCEPTANCE PASS" not in result.stdout and "US-07 QUALITY PASS" not in result.stdout


@pytest.mark.parametrize("identity", ["rewrite-01", "rewrite-13", "rewrite-24"])
def test_ac_07_8_ac_07_13_ctr05_missing_fixed_example_fails_inventory(tmp_path, identity):
    import shutil
    for directory in ("contracts", "rules", "examples", "scripts"):
        shutil.copytree(ROOT / directory, tmp_path / directory, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / f"examples/ja/{identity}.yaml").unlink()
    with pytest.raises(ValueError, match="Missing fixed rewrite examples"):
        phase1_inventory(tmp_path)
