from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

from types import SimpleNamespace

from tests.conftest import Phase1Contracts, phase1_inventory

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def suite(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\nmarkers = consumer(id): contract\n")
    source = (ROOT / "tests/conftest.py").read_text()
    source += '\ndef phase1_inventory(root):\n    return {"tests/test_consumer.py::test_cases": {"one": {"case": "one"}, "two": {"case": "two"}}}\n'
    (tmp_path / "conftest.py").write_text(source)
    marks = "\n".join(f'@pytest.mark.consumer("CTR-{n:02}")' for n in range(1, 6))
    test = 'import pytest\n' + marks + '\n@pytest.mark.parametrize("case", ["one", "two"])\ndef test_cases(case):\n    pass\n'
    (tmp_path / "tests/test_consumer.py").write_text(test)
    (tmp_path / "tests/test_other.py").write_text('import pytest\n' + marks + '\ndef test_other():\n    pass\n')
    return tmp_path


def run(suite, *args):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "COPYEDITOR_ACCEPTANCE_EVIDENCE")}
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "--require-phase1-contracts", *args],
                            cwd=suite, env=env, capture_output=True, text=True)
    assert "ACCEPTANCE PASS" not in result.stdout
    return result


@pytest.mark.parametrize("change", ["ok", "delete", "empty", "skip", "xfail", "xpass", "fail", "setup", "teardown",
                                   "partial", "subset", "collect", "duplicate", "parameter", "unconnected", "ignore"])
def test_ac_05_1_ac_05_3_ac_05_4_ctr01_ctr02_ctr03_ctr04_ctr05_strict_reports(suite, change):
    # Keep every marker on another test: markers alone must never satisfy the gate.
    path = suite / "tests/test_consumer.py"
    text = path.read_text()
    args = []
    if change == "delete":
        path.unlink()
    elif change in ("empty", "duplicate"):
        path.write_text(text.replace('["one", "two"]', '[]' if change == "empty" else '["one", "one"]'))
    elif change in ("skip", "xfail", "fail"):
        path.write_text(text.replace("    pass", {"skip": "    pytest.skip('skip')", "xfail": "    pytest.xfail('xfail')", "fail": "    assert False"}[change]))
    elif change == "parameter":
        path.write_text(text.replace('["one", "two"]', '["wrong", "two"], ids=["one", "two"]'))
    elif change == "unconnected":
        for file in (suite / "tests").glob("*.py"):
            file.write_text(file.read_text().replace('@pytest.mark.consumer("CTR-03")', ""))
    elif change == "xpass":
        path.write_text(text.replace("def test_cases", "@pytest.mark.xfail\ndef test_cases"))
    elif change in ("setup", "teardown"):
        path.write_text(text + '\n@pytest.fixture(autouse=True)\ndef broken():\n' +
                        ('    yield\n' if change == "teardown" else '') + '    raise RuntimeError("failure")\n')
    elif change == "partial": args = ["-k", "one"]
    elif change == "subset": args = ["tests/test_consumer.py"]
    elif change == "collect": args = ["--collect-only"]
    elif change == "ignore": args = ["--ignore=tests/test_other.py"]
    result = run(suite, *args)
    assert result.returncode == (0 if change == "ok" else 1), result.stdout + result.stderr
    assert ("CONTRACTS PASS" if change == "ok" else "CONTRACTS FAIL") in result.stdout


@pytest.mark.parametrize("missing", [None, "tools", "html", "examples", "language"])
def test_ac_05_1_ac_05_3_ac_05_4_ctr01_ctr02_ctr03_ctr04_ctr05_asset_inventory(tmp_path, missing):
    for directory in ("contracts", "rules", "examples", "scripts"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    if missing in ("tools", "html"):
        (tmp_path / ("contracts/tools.md" if missing == "tools" else "rules/common.md")).write_text("")
    elif missing == "examples":
        for path in (tmp_path / "examples").rglob("*.yaml"): path.unlink()
    elif missing == "language":
        (tmp_path / "rules/zh.md").unlink()
    if missing:
        with pytest.raises(Exception): phase1_inventory(tmp_path)
    else:
        inventory = phase1_inventory(tmp_path)
        assert inventory and all(inventory.values())


@pytest.fixture(scope="module")
def collected_contracts():
    class Capture:
        def pytest_collection_finish(self, session):
            self.session = session
    capture = Capture()
    # Preserve real Items and parameters; synthesized fixtures can hide inventory gaps.
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("PYTEST_ADDOPTS", raising=False)
        assert pytest.main([str(ROOT / "tests"), "--collect-only", "-q",
                            "-o", "asyncio_default_fixture_loop_scope=function"], plugins=[capture]) == 0
    session = capture.session
    session.config.option.collectonly = False
    gate = Phase1Contracts(session.config)
    gate.pytest_collection_finish(session)
    assert gate.errors == []
    return session


@pytest.mark.parametrize("module", sorted(str(p.relative_to(ROOT)) for p in ROOT.glob("tests/*/test_*.py") if p.name not in ("test_harness.py", "test_phase1.py")))
def test_ac_05_1_ac_05_3_ac_05_4_ctr01_ctr02_ctr03_ctr04_ctr05_real_consumer_deletion(collected_contracts, module):
    gate = Phase1Contracts(collected_contracts.config)
    remaining = [item for item in collected_contracts.items if item.nodeid.split("::")[0] != module]
    assert len(remaining) < len(collected_contracts.items)
    gate.pytest_collection_finish(SimpleNamespace(items=remaining))
    assert any(error.startswith("Inventory mismatch: " + module + "::") for error in gate.errors)


def test_ctr02_preservation_deletion_fails_through_cli():
    module = "tests/contracts/test_ctr02_preservation.py"
    result = run(ROOT, "tests", "--collect-only", "--ignore=" + module)
    assert result.returncode == 1
    assert "CONTRACTS FAIL" in result.stdout
    assert "Inventory mismatch: " + module + "::" in result.stdout
