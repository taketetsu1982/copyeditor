import io
import json
from pathlib import Path
import runpy
import stat
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

SHA, OTHER = "a" * 40, "b" * 40
REPO, TAG = "Owner/copyeditor", "v0.1.0"
STAMP = "2026-09-17T01:00:00Z"
ROOT = "repos/" + REPO
ATTEMPT = ROOT + "/actions/runs/12/attempts/1"
MESSAGE = "GitHub evidence could not be read or validated."


@pytest.fixture
def release(monkeypatch, capsys):
    module = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/check_evidence.py"))
    data = dict(schema="copyeditor-release-evidence-v1", repository=REPO, run_id=12, run_attempt=1,
                workflow="Publish image", commit=SHA, tag=TAG, image="ghcr.io/owner/copyeditor:" + TAG,
                digest="sha256:" + "c" * 64, published_at=STAMP)
    run = dict(id=12, run_attempt=1, status="completed", conclusion="success", event="push", head_sha=SHA,
               name="Publish image", head_branch=TAG, repository={"full_name": REPO}, workflow_id=7,
               path=".github/workflows/publish.yml@refs/tags/v0.1.0", updated_at="PRIVATE_DETAIL")
    state = dict(data=data, run=run, workflow=dict(path=".github/workflows/publish.yml", name="Publish image"),
                 artifact=dict(id=5, name="release-evidence-" + TAG, expired=False),
                 job=dict(name="publish", conclusion="success", started_at=STAMP, completed_at=STAMP),
                 failure=None, archive=None, raw=None, filenames=["release-evidence.json"], mode=stat.S_IFREG,
                 duplicate=None, missing=None, resolved=REPO, target=dict(type="commit", sha=SHA), annotated=False)
    calls = []
    def fake(command, **kwargs):
        assert command[:2] == ["gh", "api"] and command[3:5] == ["--method", "GET"]
        endpoint = command[2]
        calls.append(endpoint)
        assert kwargs["capture_output"] and kwargs["check"] and kwargs["timeout"] == 60
        if state["failure"] and state["failure"] in endpoint: raise OSError("PRIVATE_DETAIL")
        if endpoint.endswith("/zip"):
            assert endpoint == ROOT + "/actions/artifacts/5/zip"
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                for filename in state["filenames"]:
                    info = zipfile.ZipInfo(filename)
                    info.external_attr = (state["mode"] | 0o600) << 16
                    archive.writestr(info, state["raw"] if state["raw"] is not None else json.dumps(state["data"]))
            return SimpleNamespace(stdout=state["archive"] if state["archive"] is not None else stream.getvalue())
        if endpoint == "repos/{owner}/{repo}": value = dict(full_name=state["resolved"])
        elif endpoint == ATTEMPT: value = state["run"]
        elif endpoint == ROOT + "/actions/runs/12/attempts/2": value = dict(state["run"], run_attempt=2, conclusion="failure")
        elif endpoint == ROOT + "/actions/workflows/7": value = state["workflow"]
        elif endpoint == ROOT + "/git/ref/tags/" + TAG:
            value = dict(object=dict(type="tag", sha=OTHER) if state["annotated"] else state["target"])
        elif endpoint == ROOT + "/git/tags/" + OTHER: value = dict(object=state["target"])
        else:
            key, item = ("artifacts", "artifact") if "/artifacts?" in endpoint else ("jobs", "job")
            assert endpoint == (ROOT + "/actions/runs/12/artifacts" if key == "artifacts" else ATTEMPT + "/jobs") + "?per_page=100"
            assert command[5:] == ["--paginate", "--slurp"]
            records = [] if state["missing"] == item else [state[item]]
            value = [{key: records if state["duplicate"] == item else []}, {key: records}]
        return SimpleNamespace(stdout=json.dumps(value))
    monkeypatch.setattr(subprocess, "run", fake)
    def invoke(ok=True, attempt=1):
        if ok: result = module["load_release_evidence"](REPO, 12, attempt, TAG)
        else:
            with pytest.raises(ValueError, match="^" + MESSAGE.replace(".", r"\.") + "$") as error:
                module["load_release_evidence"](REPO, 12, attempt, TAG)
            assert error.value.__suppress_context__
            result = None
        assert capsys.readouterr() == ("", "")
        return result
    return state, invoke, module, calls


@pytest.mark.parametrize("annotated", [False, True])
def test_ac_05_1_ac_05_3_ac_05_4_ctr03_ctr05_selected_attempt_and_boundaries(release, annotated):
    state, invoke, module, calls = release
    state["annotated"] = annotated
    assert invoke() == state["data"]
    assert ATTEMPT in calls and all(not c.endswith("/actions/runs/12") for c in calls)
    invoke(False, attempt=2)
    state["job"].update(started_at="2026-09-17T00:59:00Z", completed_at="2026-09-17T01:01:00Z")
    assert invoke() == state["data"]


@pytest.mark.parametrize("section,key,value", [
    ("data", "run_id", 13), ("data", "run_attempt", 2), ("data", "commit", OTHER), ("data", "tag", "v0.2.0"),
    ("data", "repository", "Other/repo"), ("data", "digest", "PRIVATE_DETAIL"), ("data", "extra", "PRIVATE_DETAIL"),
    ("data", "schema", "unknown"), ("data", "image", "other"), ("data", "workflow", "Other"),
    ("data", "run_id", True), ("data", "run_attempt", 1.0), ("data", "published_at", "2026-09-17T00:59:59Z"),
    ("data", "published_at", "2026-09-17T01:00:01Z"), ("data", "published_at", "2026-02-30T01:00:00Z"),
    ("run", "id", 13), ("run", "event", "workflow_dispatch"), ("run", "status", "in_progress"),
    ("run", "head_branch", "v0.2.0"), ("run", "workflow_id", None), ("run", "workflow_id", True),
    ("run", "workflow_id", 0), ("run", "workflow_id", "7"), ("run", "workflow_id", 7.0),
    ("workflow", "path", ".github/workflows/other.yml"), ("workflow", "name", "Other"),
    ("artifact", "expired", True), ("artifact", "id", False), ("job", "conclusion", "failure"),
    ("job", "started_at", None), ("job", "completed_at", None),
    ("job", "started_at", "2026-09-18T00:00:00Z"), ("job", "completed_at", "2026-09-16T01:00:00Z"),
    ("target", "sha", OTHER), ("target", "type", "blob")])
def test_ac_05_1_ac_05_3_ac_05_4_ctr03_ctr05_mismatched_metadata(release, section, key, value):
    state, invoke, _, _ = release
    state[section][key] = value
    invoke(False)


@pytest.mark.parametrize("key,value", [
    ("missing", "artifact"), ("duplicate", "artifact"), ("missing", "job"), ("duplicate", "job"),
    ("resolved", "Other/repo"), ("failure", "workflows"), ("failure", "artifacts"), ("failure", "jobs"),
    ("failure", "/zip"), ("failure", "/attempts/"), ("failure", "/git/ref/"),
    ("workflow", None), ("archive", b"PRIVATE_DETAIL"), ("raw", '{"schema":1,"schema":2}'),
    ("raw", "PRIVATE_DETAIL"), ("filenames", ["../release-evidence.json"]),
    ("filenames", ["release-evidence.json", "other"]), ("filenames", []), ("mode", stat.S_IFLNK),
    ("target", dict(type="tag", sha=OTHER))])
def test_ac_05_1_ac_05_3_ac_05_4_ctr03_ctr05_unavailable_or_unsafe_evidence(release, key, value):
    state, invoke, _, _ = release
    state[key] = value
    invoke(False)


def test_ac_05_1_ac_05_3_ac_05_4_ctr03_ctr05_missing_and_null_fields(release):
    state, invoke, _, _ = release
    for key in list(state["data"]):
        original = state["data"].pop(key)
        invoke(False)
        state["data"][key] = None
        invoke(False)
        state["data"][key] = original
    del state["run"]["workflow_id"]
    invoke(False)


def test_ac_05_1_ac_05_3_ac_05_4_ctr03_ctr05_run_url_is_repository_and_attempt_bound(release):
    _, _, module, _ = release
    parse = module["parse_release_run_url"]
    url = f"https://github.com/{REPO}/actions/runs/12/attempts/1"
    assert parse(REPO, url) == (12, 1)
    for bad in (url.split("/attempts")[0], url.replace("github.com", "evil.test"), url.replace(REPO, "Other/repo"),
                url + "?token=PRIVATE_DETAIL", url + "/", url.replace("runs/12", "runs/0"), url[:-1] + "0"):
        with pytest.raises(ValueError, match="GitHub evidence could not be read or validated"):
            parse(REPO, bad)
