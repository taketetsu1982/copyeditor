import io
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError
import urllib.request

import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]
PUBLISH = YAML(typ="safe").load((ROOT / ".github/workflows/publish.yml").read_text())
CI = YAML(typ="safe").load((ROOT / ".github/workflows/ci.yml").read_text())
GATE = PUBLISH["jobs"]["gate"]["steps"][-1]["run"]
PUSH = PUBLISH["jobs"]["publish"]["steps"][-1]["run"]
SHA = "a" * 40


def test_ac_05_7_ctr04_distribution_permissions_and_order():
    assert PUBLISH["on"] == {"push": {"tags": ["v*"]}}
    assert "workflow_call" in CI["on"] and CI["permissions"] == {"contents": "read"}
    assert PUBLISH["permissions"] == {"contents": "read"}
    assert PUBLISH["concurrency"] == {"group": "publish-${{ github.repository }}-${{ github.ref }}", "cancel-in-progress": False}
    jobs = PUBLISH["jobs"]
    assert jobs["checks"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["gate"]["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert jobs["publish"]["permissions"] == {"contents": "read", "packages": "write"}
    assert jobs["publish"]["needs"] == ["checks", "gate"]
    assert jobs["publish"]["if"] == ("github.event_name == 'push' && github.ref_type == 'tag' && "
                                    "needs.checks.result == 'success' && needs.gate.result == 'success'")
    assert jobs["gate"]["if"] == "github.event_name == 'push' && github.ref_type == 'tag'"
    assert jobs["publish"]["steps"][0]["with"]["ref"] == "${{ needs.gate.outputs.commit }}"
    runs = [s["run"] for job in CI["jobs"].values() for s in job["steps"] if "run" in s]
    assert {"python -m pytest -q", "bash scripts/test_images.sh", "npm run test:fixtures"} <= set(runs)
    assert all(j["steps"][0]["with"]["ref"] == "${{ github.sha }}" for j in CI["jobs"].values())
    assert not any("continue-on-error" in s or "if" in s for j in CI["jobs"].values() for s in j["steps"])
    readme = (ROOT / "README.md").read_text()
    assert "copyeditor:local" in readme and "copyeditor:custom" in readme
    assert "contracts/config.md" in readme


@pytest.fixture
def runner(monkeypatch, tmp_path):
    values = dict(GITHUB_EVENT_NAME="push", GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v0.1.0",
                  GITHUB_REPOSITORY_OWNER="Owner", GITHUB_REPOSITORY="Owner/copyeditor", GITHUB_ACTOR="actor",
                  COPYEDITOR_OWNER="owner", COPYEDITOR_NATIVE_PR="7", COPYEDITOR_PROVENANCE_PR="8",
                  GITHUB_OUTPUT=str(tmp_path / "outputs"), GH_TOKEN="synthetic", IMAGE="ghcr.io/owner/copyeditor:v0.1.0")
    for k, v in values.items(): monkeypatch.setenv(k, v)
    state = dict(commands=[], requests=[], failure=None, status=404, codes=["MANIFEST_UNKNOWN"], token="synthetic")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: SHA + "\n")
    def command(args, **kwargs):
        state["commands"].append(args)
        stage = "gate" if args[0] == "python" else next(x for x in ("build", "login", "push") if x in args)
        if stage == "gate":
            assert args[1:] == ["scripts/check_evidence.py", "publish-gate", "--native-pr", "7",
                               "--provenance-pr", "8", "--owner", "owner", "--commit", SHA]
        if state["failure"] == stage: raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(subprocess, "run", command)
    def request(req, **kwargs):
        state["requests"].append(req.full_url)
        assert kwargs["timeout"] == 30
        if state["failure"] == "network": raise OSError("PRIVATE_DETAIL")
        if "/token?" in req.full_url: return io.BytesIO(json.dumps({"token": state["token"]}).encode())
        assert req.get_header("Authorization") == "Bearer synthetic"
        if state["status"] == 200: return io.BytesIO(b"{}")
        body = b"not json" if state["failure"] == "malformed" else json.dumps({"errors": [{"code": c} for c in state["codes"]]}).encode()
        raise HTTPError(req.full_url, state["status"], "PRIVATE_DETAIL", {}, io.BytesIO(body))
    monkeypatch.setattr(urllib.request, "urlopen", request)
    return state, monkeypatch, tmp_path


@pytest.mark.parametrize("tag", ["v0.1.0", "v1.2.3", "v0.0.9", "v01.2.3", "v1.2", "v1.2.3-rc1", "v1.2.3+meta", "v1.2.3\n"])
def test_ac_05_7_ctr04_tag_gate(runner, tag):
    state, patch, path = runner
    patch.setenv("GITHUB_REF_NAME", tag)
    if tag in ("v0.1.0", "v1.2.3"):
        exec(GATE, {})
        assert (path / "outputs").read_text() == f"commit={SHA}\nimage=ghcr.io/owner/copyeditor:{tag}\n"
    else:
        with pytest.raises(AssertionError): exec(GATE, {})
        assert not state["commands"]


@pytest.mark.parametrize("failure", ["pr", "branch", "gate"])
def test_ac_05_7_ctr04_rejected_gate_has_no_publish_output(runner, failure):
    state, patch, path = runner
    if failure == "pr": patch.setenv("GITHUB_EVENT_NAME", "pull_request")
    if failure == "branch": patch.setenv("GITHUB_REF_TYPE", "branch")
    state["failure"] = failure
    with pytest.raises((AssertionError, subprocess.CalledProcessError)): exec(GATE, {})
    assert not (path / "outputs").exists()


@pytest.mark.parametrize("case", ["absent", "new", "exists", "unauthorized", "server", "unknown", "empty", "network", "malformed", "token", "build", "login"])
def test_ac_05_7_ctr04_registry_absence_is_required_immediately_before_push(runner, case):
    state, patch, path = runner
    if case == "new": state["codes"] = ["NAME_UNKNOWN"]
    if case in ("exists", "unauthorized", "server"): state["status"] = {"exists": 200, "unauthorized": 401, "server": 500}[case]
    if case == "unknown": state["codes"] = ["DENIED"]
    if case == "empty": state["codes"] = []
    if case == "token": state["token"] = None
    state["failure"] = case
    if case in ("absent", "new"):
        exec(PUSH, {})
        assert state["commands"][-1][-2:] == ["push", "ghcr.io/owner/copyeditor:v0.1.0"]
        assert state["requests"][-1].endswith("/manifests/v0.1.0")
    else:
        with pytest.raises(SystemExit, match="Publication failed"): exec(PUSH, {})
        assert not any("push" in c for c in state["commands"])


@pytest.mark.parametrize("failed", ["python -m pytest -q", "bash scripts/test_images.sh", "npm run test:fixtures"])
def test_ac_05_7_ctr04_each_ci_failure_prevents_publish_sequence(runner, failed):
    state, patch, path = runner
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command == ["bash", "-e", "-c", failed]: raise subprocess.CalledProcessError(1, command)
    patch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        for job in CI["jobs"].values():
            for step in job["steps"]:
                if "run" in step: subprocess.run(["bash", "-e", "-c", step["run"]], check=True)
        exec(GATE, {})
        exec(PUSH, {})
    assert not any("push" in command for command in calls)
