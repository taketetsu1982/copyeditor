import io
import json
import subprocess
import shlex
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
import urllib.request

import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]
PUBLISH = YAML(typ="safe").load((ROOT / ".github/workflows/publish.yml").read_text())
CI = YAML(typ="safe").load((ROOT / ".github/workflows/ci.yml").read_text())
GATE = PUBLISH["jobs"]["gate"]["steps"][-1]["run"]
PUSH = next(s["run"] for s in PUBLISH["jobs"]["publish"]["steps"] if s.get("shell") == "python")
SHA = "a" * 40
IMAGE_ID = "sha256:" + "b" * 64
DIGEST = "sha256:" + "c" * 64


def test_ac_05_7_ctr04_distribution_permissions_and_order():
    assert PUBLISH["on"] == {"push": {"tags": ["v*"]}}
    assert set(CI["on"]) == {"push", "pull_request", "workflow_call"}
    assert CI["on"]["push"] == {"branches": ["main"]}
    assert CI["permissions"] == {"contents": "read"}
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
    suites = [shlex.split(command) for command in runs if "pytest" in shlex.split(command)]
    assert len(suites) == 1
    assert suites[0][:4] == ["python", "-m", "pytest", "tests"]
    assert "--require-phase1-contracts" in suites[0] and "--durations=20" in suites[0]
    assert not any(flag in suites[0] for flag in ("--collect-only", "--require-phase1-acceptance", "--ignore", "-k"))
    assert not any(driver in command for command in runs for driver in ("test_images.sh", "test:fixtures"))
    assert all(j["steps"][0]["with"]["ref"] == "${{ github.sha }}" for j in CI["jobs"].values())
    assert not any("continue-on-error" in s or "if" in s for j in CI["jobs"].values() for s in j["steps"])
    readme = (ROOT / "README.md").read_text()
    assert "copyeditor:local" in readme and "copyeditor:custom" in readme
    assert "contracts/config.md" in readme


@pytest.fixture
def runner(monkeypatch, tmp_path):
    values = dict(GITHUB_EVENT_NAME="push", GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v0.1.0",
                  GITHUB_REPOSITORY_OWNER="Owner", GITHUB_REPOSITORY="Owner/copyeditor", GITHUB_ACTOR="actor",
                  COPYEDITOR_OWNER="owner", COPYEDITOR_NATIVE_PR="7",
                  GITHUB_OUTPUT=str(tmp_path / "outputs"), GH_TOKEN="synthetic", IMAGE="ghcr.io/owner/copyeditor:v0.1.0")
    values.update(GATE_COMMIT=SHA, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2",
                  GITHUB_WORKFLOW="Publish image", RUNNER_TEMP=str(tmp_path))
    for k, v in values.items(): monkeypatch.setenv(k, v)
    state = dict(commands=[], requests=[], failure=None, status=404, codes=["MANIFEST_UNKNOWN"], token="synthetic")
    state.update(inspects=[], digests=["ghcr.io/owner/copyeditor@" + DIGEST], retag=False)
    def output(args, **kwargs):
        if args[0] == "git": return SHA + "\n"
        assert args[:-1] == ["docker", "image", "inspect", "--format", "{{json .}}"]
        state["inspects"].append(args[-1])
        pushed = any("push" in c for c in state["commands"])
        assert args[-1] == (IMAGE_ID if pushed else values["IMAGE"])
        assert not (tmp_path / "release-evidence.json").exists()
        if state["failure"] == "inspect": raise subprocess.CalledProcessError(1, args)
        if state["failure"] == "digest-inspect" and pushed: raise subprocess.CalledProcessError(1, args)
        changed = state["retag"] and len(state["inspects"]) == 2
        return json.dumps({"Id": "sha256:" + "d" * 64 if changed else IMAGE_ID, "RepoDigests": state["digests"]})
    monkeypatch.setattr(subprocess, "check_output", output)
    def command(args, **kwargs):
        state["commands"].append(args)
        stage = "gate" if args[0] == "python" else next(x for x in ("build", "login", "push") if x in args)
        if stage == "gate":
            assert args[1:] == ["scripts/check_evidence.py", "publish-gate", "--native-pr", "7",
                               "--owner", "owner", "--commit", SHA]
        if state["failure"] == stage: raise subprocess.CalledProcessError(1, args)
        if stage == "push":
            assert state["inspects"] == [values["IMAGE"], values["IMAGE"]]
            assert not (tmp_path / "release-evidence.json").exists()
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
    assert not (path / "release-evidence.json").exists()


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
        assert not (path / "release-evidence.json").exists()


@pytest.mark.parametrize("failed", [s["run"] for job in CI["jobs"].values() for s in job["steps"] if "run" in s])
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


def test_ac_05_1_release_evidence_binds_context_and_manifest(runner):
    state, patch, path = runner
    before = datetime.now(timezone.utc).replace(microsecond=0)
    namespace = {}
    exec(PUSH, namespace)
    evidence = json.loads((path / "release-evidence.json").read_text())
    published = datetime.strptime(evidence["published_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert before <= published <= datetime.now(timezone.utc)
    assert evidence == dict(schema="copyeditor-release-evidence-v1", repository="Owner/copyeditor",
                            run_id=123, run_attempt=2, workflow="Publish image", commit=SHA, tag="v0.1.0",
                            image="ghcr.io/owner/copyeditor:v0.1.0", digest=DIGEST, published_at=evidence["published_at"])
    assert state["inspects"][-1] == IMAGE_ID and evidence["digest"] != IMAGE_ID
    for key in evidence:
        for invalid in ({**evidence, key: None}, {k: v for k, v in evidence.items() if k != key}):
            with pytest.raises(AssertionError): namespace["validate_evidence"](invalid)
    for change in ({"extra": "forbidden"}, {"run_id": True}, {"run_attempt": 0}, {"run_id": "123"},
                   {"commit": "a" * 39}, {"digest": IMAGE_ID.upper()}, {"tag": "v01.2.3"},
                   {"workflow": "Other"}, {"repository": "Other/repo"}, {"published_at": "2026-02-30T00:00:00Z"}):
        with pytest.raises((AssertionError, ValueError)): namespace["validate_evidence"]({**evidence, **change})


@pytest.mark.parametrize("case", ["empty", "malformed", "multiple", "other-repository", "null", "inspect",
                                  "digest-inspect", "retag", "push", "commit", "context"])
def test_ac_05_1_invalid_producer_state_never_writes_evidence(runner, case):
    state, patch, path = runner
    if case == "empty": state["digests"] = []
    if case == "malformed": state["digests"] = ["ghcr.io/owner/copyeditor@sha256:bad"]
    if case == "multiple": state["digests"].append("ghcr.io/owner/copyeditor@" + IMAGE_ID)
    if case == "other-repository": state["digests"] = ["ghcr.io/other/copyeditor@" + DIGEST]
    if case == "null": state["digests"] = None
    if case == "retag": state["retag"] = True
    if case == "commit": patch.setenv("GATE_COMMIT", "d" * 40)
    if case == "context": patch.setenv("GITHUB_WORKFLOW", "Other")
    state["failure"] = case
    with pytest.raises(SystemExit, match="Publication failed"): exec(PUSH, {})
    assert not (path / "release-evidence.json").exists()
    if case in ("retag", "inspect", "commit"): assert not any("push" in c for c in state["commands"])


def test_ac_05_1_duplicate_digest_is_unique_and_unrelated_repository_is_ignored(runner):
    state, patch, path = runner
    state["digests"] *= 2
    state["digests"].append("ghcr.io/other/copyeditor@" + IMAGE_ID)
    exec(PUSH, {})
    assert json.loads((path / "release-evidence.json").read_text())["digest"] == DIGEST


def test_ac_05_1_upload_failure_cannot_complete_publication(runner):
    state, patch, path = runner
    job = PUBLISH["jobs"]["publish"]
    upload = job["steps"][-1]
    assert "name" not in job and "continue-on-error" not in job
    assert upload == {"uses": "actions/upload-artifact@v4", "with": {
        "name": "release-evidence-${{ github.ref_name }}", "path": "${{ runner.temp }}/release-evidence.json",
        "if-no-files-found": "error", "retention-days": 90, "overwrite": False}}
    completed = []
    with pytest.raises(RuntimeError, match="Upload failed"):
        for step in job["steps"][2:]:
            assert "continue-on-error" not in step and "if" not in step
            if "run" in step: exec(step["run"], {})
            else:
                assert (path / "release-evidence.json").is_file()
                raise RuntimeError("Upload failed")
        completed.append(True)
    assert not completed
