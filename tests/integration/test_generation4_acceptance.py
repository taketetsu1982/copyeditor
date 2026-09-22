"""Must/contract and Should verification ownership; machine success is not owner acceptance."""
from collections import Counter
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from tests.conftest import GENERATION4_MODULES, US08_OWNER_EVIDENCE, Phase1Contracts, phase1_inventory, SHOULD_MODULES, judgment_fingerprint
from tests.integration.test_phase1 import collected_contracts, run, suite

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "deployment/generation4-acceptance.md"
MUST = {
    "01": (1, 3, 4, 5, 6, 7, 8, 9, 10, 11),
    "02": (1, 2, 3, 4, 5, 6, 8, 10, 11, 14, 15, 16, 17, 18),
    "04": (1, 3, 4, 5, 7),
    "05": (1, 2, 3, 4, 5, 7, 8, 9, 10),
    "06": (1, 3, 4),
    "07": (3, 4, 6, 11, 12),
    "08": (1, 3, 5, 6, 7, 8, 9, 11, 12, 18),
}
OWNED = {f"AC-{group}-{number}" for group, numbers in MUST.items() for number in numbers} | {"CTR-01", "CTR-02", "CTR-04"}


SHOULD = {
    "01": (13,), "02": (7, 9, 19), "03": (1, 2, 3, 4), "04": (2, 6),
    "05": (6,), "06": (2, 5, 6), "07": (1, 2, 7, 8, 9, 10, 13),
    "08": (2, 10, 13, 14, 15, 16),
}
SHOULD_IDS = {f"AC-{group}-{number}" for group, numbers in SHOULD.items() for number in numbers}


def ownership(section="Must and contract ownership"):
    rows = []
    text = DOCUMENT.read_text().split("## " + section + "\n", 1)[1].split("\n## ", 1)[0]
    for line in text.splitlines():
        if line.startswith(("| AC-", "| CTR-")):
            ids, consumers, owners, description = [part.strip() for part in line.strip("|").split("|")]
            rows.append((ids.split(", "), re.findall(r"`(tests/[^`]+\.py)`", consumers), owners, description))
    return rows


def test_all_must_and_contract_ids_have_one_current_owner_and_collected_consumers(collected_contracts):
    rows = ownership()
    assert Counter(identity for ids, _, _, _ in rows for identity in ids) == Counter(OWNED)
    collected = {item.nodeid.split("::")[0] for item in collected_contracts.items}
    for ids, modules, owners, description in rows:
        assert modules and set(modules) <= collected and description
        assert set(owners.split(", ")) <= {"none", "native", "client", "deployment", "release"}
        assert all((ROOT / module).is_file() for module in modules)
    assert set(GENERATION4_MODULES) <= collected
    assert all(module + "::*" in phase1_inventory(ROOT) for module in GENERATION4_MODULES)


@pytest.mark.parametrize("module", sorted({m for _, modules, _, _ in ownership() for m in modules}))
def test_owned_consumer_requires_successful_execution_not_just_collection(collected_contracts, module):
    item = next(item for item in collected_contracts.items if item.nodeid.split("::")[0] == module)
    gate = Phase1Contracts(collected_contracts.config)
    gate.items = [item]
    for when in ("setup", "call", "teardown"):
        gate.pytest_runtest_logreport(SimpleNamespace(nodeid=item.nodeid, when=when, passed=when != "call"))
    session = SimpleNamespace(exitstatus=0)
    gate.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED


def test_strict_machine_success_keeps_native_client_deployment_and_release_pending(suite, monkeypatch):
    for key in ("TYPESAFE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_API_KEY", "COPYEDITOR_OWNER"):
        monkeypatch.delenv(key, raising=False)
    result = run(suite, "tests")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONTRACTS PASS" in result.stdout and "ACCEPTANCE PASS" not in result.stdout
    for kind in ("native", "client", "deployment", "release"):
        assert f"US-08 {kind}: NOT EVALUATED" in result.stdout
        assert kind in US08_OWNER_EVIDENCE
    pending = run(suite, "tests", "--require-phase1-acceptance")
    assert pending.returncode != 0 and "ACCEPTANCE PASS" not in pending.stdout


def test_owner_checklist_preserves_current_asset_identity_and_existing_gates():
    text = DOCUMENT.read_text()
    for phrase in ("built-in default style", "both Claude and Codex", "non-delivery", "fresh Vertex project/device",
                   "stock and derived images", "Google authentication", "publication/release evidence",
                   "source commit", "versions and hashes", "previous release or PR merge cannot certify changed assets",
                   "explicit owner authorization", "Do not publish or tag", "without transferring this ownership"):
        assert phrase in text


def test_all_27_should_ids_have_distinct_ownership_and_real_consumers(collected_contracts):
    rows = ownership("Should ownership")
    assert len(SHOULD_IDS) == 27 and not SHOULD_IDS.intersection(OWNED)
    assert Counter(identity for ids, _, _, _ in rows for identity in ids) == Counter(SHOULD_IDS)
    collected = {item.nodeid.split("::")[0] for item in collected_contracts.items}
    fixed = {entry.split("::")[0] for entry in phase1_inventory(ROOT)}
    for ids, modules, owners, description in rows:
        assert modules and set(modules) <= collected.intersection(fixed) and description
        assert set(owners.split(", ")) <= {"none", "live", "native", "client"}
    for module, expected in SHOULD_MODULES.items():
        assert judgment_fingerprint(ROOT, module, collected_contracts.items) == expected


@pytest.mark.parametrize("module", sorted(SHOULD_MODULES))
@pytest.mark.parametrize("change", ["module", "case", "replacement"])
def test_missing_or_replaced_should_consumer_cannot_pass(collected_contracts, module, change):
    items = list(collected_contracts.items)
    selected = next(item for item in items if item.nodeid.split("::")[0] == module)
    if change == "module": items = [item for item in items if item.nodeid.split("::")[0] != module]
    elif change == "case": items.remove(selected)
    else: items[items.index(selected)] = SimpleNamespace(nodeid=selected.nodeid, iter_markers=selected.iter_markers, callspec=SimpleNamespace(id="replacement", params={"replacement": True}))
    gate = Phase1Contracts(collected_contracts.config)
    gate.pytest_collection_finish(SimpleNamespace(items=items))
    assert "Inventory mismatch: " + module + "::*" in gate.errors


def test_should_checklist_keeps_quality_and_actual_client_results_unaccepted():
    text = DOCUMENT.read_text().split("## Should ownership\n", 1)[1]
    for phrase in ("all 27", "Task 166", "NOT EVALUATED", "calibration remeasurement",
                   "unused held-out", "both Claude and Codex", "4/5", "24/30", "50",
                   "15/18", "all-KEEP", "source commit", "versions and hashes"):
        assert phrase in text


def test_ac_02_7_initialization_guide_is_complete_within_first_512_characters():
    from copyeditor.server import INSTRUCTIONS
    for text in (INSTRUCTIONS, INSTRUCTIONS.replace("Vertex AI.", "Vertex AI and TypeSafe AI.")):
        prefix = text[:512]
        for phrase in ("Vertex AI", "does not persist", "providers govern retention", "items [{id,text,context?}]",
                       "html uses text only", "Set language", "Compare results before applying"):
            assert phrase in prefix


def test_ac_05_6_low_thinking_default_cannot_be_overridden_by_a_tool_request(tmp_path):
    from copyeditor.config_v4 import load_config
    from copyeditor.rules import load_rules
    from copyeditor.requests import parse_edit_request, ValidationError
    config = load_config(tmp_path / "absent.yaml", {"GOOGLE_CLOUD_PROJECT": "synthetic-project"})
    assert config["thinking"] == "low"
    rules = load_rules(ROOT / "rules", None)
    for degree in ("polish", "rewrite"):
        with pytest.raises(ValidationError):
            parse_edit_request("polish_text", dict(text="Synthetic text.", language="en", degree=degree, thinking="high"), config, rules)
    assert config["thinking"] == "low"


def test_ac_04_6_contributor_guide_uses_real_contracts_and_states_ci_limits():
    text = (ROOT / "CONTRIBUTING.md").read_text()
    for phrase in ("include an example in the same pull request", "examples/<lang>/<id>.yaml",
                   "bad", "good", "reason", "lint: null", "rule_ids", "rewrite_expectations.invariants",
                   "--require-phase1-contracts", "bad-detected/good-not-detected", "does not prove naturalness"):
        assert phrase in text
    targets = re.findall(r"\]\(([^)]+)\)", text)
    assert {"rules/README.md", "examples/README.md"} <= set(targets)
    assert all(not target.startswith("docs/") and (ROOT / target).is_file() for target in targets)
