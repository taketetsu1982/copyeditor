"""Must/contract verification ownership; machine success is not owner acceptance."""
from collections import Counter
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from tests.conftest import GENERATION4_MODULES, US08_OWNER_EVIDENCE, Phase1Contracts, phase1_inventory
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


def ownership():
    rows = []
    for line in DOCUMENT.read_text().splitlines():
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
