import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci_scope.py"
spec = importlib.util.spec_from_file_location("ci_scope", SCRIPT)
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


@pytest.mark.parametrize("event,base,paths,expected", [
    ("pull_request", "a" * 40, b"README.md\0", "docs"),
    ("push", "a" * 40, b"README.md\0", "full"),
    ("workflow_call", "a" * 40, b"README.md\0", "full"),
    ("pull_request", "", b"README.md\0", "full"),
    ("pull_request", "--invalid", b"README.md\0", "full"),
    ("pull_request", "a" * 40, b"", "full"),
    ("pull_request", "a" * 40, b"README.md\0src/new.py\0", "full"),
    ("pull_request", "a" * 40, b"rules/ja.md\0", "full"),
    ("pull_request", "a" * 40, b"contracts/tools.md\0", "full"),
    ("pull_request", "a" * 40, b".github/workflows/ci.yml\0", "full"),
    ("pull_request", "a" * 40, b"requirements.generated.txt\0", "full"),
    ("pull_request", "a" * 40, b"unknown.md\0", "full"),
    ("pull_request", "a" * 40, b"bad\xff\0", "full"),
])
def test_only_known_documentation_prs_use_reduced_checks(monkeypatch, event, base, paths, expected):
    monkeypatch.setattr(scope.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, paths))
    assert scope.select_scope(event, base, "HEAD") == expected


def test_unavailable_diff_keeps_full_checks(monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(128, args)
    monkeypatch.setattr(scope.subprocess, "run", failed)
    assert scope.select_scope("pull_request", "a" * 40, "HEAD") == "full"


def test_cli_checks_both_paths_of_renames_and_reports_to_actions(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                        *args], cwd=tmp_path, text=True).strip()
    git("init", "-q")
    (tmp_path / "README.md").write_text("Before\n")
    git("add", ".")
    git("commit", "-qm", "Initial")
    base = git("rev-parse", "HEAD")
    (tmp_path / "README.md").write_text("After\n")
    git("commit", "-qam", "Documentation")
    output = tmp_path / "output"
    env = dict(os.environ, GITHUB_EVENT_NAME="pull_request", PR_BASE_SHA=base, GITHUB_OUTPUT=str(output))
    subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, env=env, check=True)
    assert output.read_text() == "scope=docs\n"
    git("mv", "README.md", "runtime.py")
    git("commit", "-qm", "Rename")
    subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, env=env, check=True)
    assert output.read_text().endswith("scope=full\n")
