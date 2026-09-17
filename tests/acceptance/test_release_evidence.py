import io
import base64
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


@pytest.fixture
def acceptance(release, monkeypatch, tmp_path, capsys):
    state, _, module, _ = release
    native, provenance, blob, comparison = [c * 40 for c in "def0"]
    assets = {"rules/ja.md": blob, "examples/ja/a.yaml": blob, "rules/en.md": blob, "examples/zh/a.yaml": blob}
    state.update(assets={sha: dict(assets) for sha in (SHA, native, provenance)}, approval_time=STAMP,
                 approval_owner="owner", native=True, refusal=False, absent=False, acceptance_raw=None)
    owner_data = dict(schema="copyeditor-acceptance-evidence-v1", owner="owner", recorded_at=STAMP, repository=REPO,
                      run_url=f"https://github.com/{REPO}/actions/runs/12/attempts/1", run_attempt=1,
                      commit=SHA, tag=TAG, digest=state["data"]["digest"], native_pr_url=f"https://github.com/{REPO}/pull/7",
                      provenance_pr_url=f"https://github.com/{REPO}/pull/8", checks=dict.fromkeys(
                          ["google_oauth", "allowed_domain", "allowed_email", "anonymous_401", "none_derived_polish", "google_derived_polish"], True))
    original = subprocess.run
    def fake(command, **kwargs):
        endpoint = command[2]
        if endpoint.startswith("repos/coji/"): value = dict(sha=comparison)
        elif not endpoint.startswith("repos/{owner}/{repo}/"): return original(command, **kwargs)
        elif "/pulls/" in endpoint and "/reviews" not in endpoint:
            value = dict(user=dict(login="owner"), head=dict(sha=native if endpoint.endswith("/7") else provenance))
        elif "/git/commits/" in endpoint:
            sha = endpoint.rsplit("/", 1)[1]
            value = dict(sha=sha, tree=dict(sha=sha))
        elif "/git/trees/" in endpoint:
            sha = endpoint.rsplit("/", 1)[1].split("?")[0]
            value = dict(truncated=False, tree=[dict(path=p, sha=h, type="blob", mode="100644")
                         for p, h in (state["assets"][sha] | {"README.md": blob}).items()])
        elif "/git/blobs/" in endpoint:
            value = dict(encoding="base64", content=base64.b64encode((module["ACK_EN"] + module["ACK_JA"]).encode()).decode())
        else:
            records = []
            if "/issues/7/" in endpoint and state["native"]:
                records = [dict(user=dict(login="owner"), updated_at=STAMP,
                                body="Head: " + native + "\n" + "\n".join(s + ": approved" for s in module["SECTIONS"]))]
            if "/issues/8/" in endpoint:
                proof = dict(head=provenance, comparison_revision=comparison, non_reuse="confirmed", blobs=state["assets"][provenance])
                body = "```copyeditor-provenance-v1\n" + json.dumps(proof) + "\n```"
                records = [dict(user=dict(login=state["approval_owner"]), updated_at=state["approval_time"], body=body)]
                if state["refusal"]:
                    records.append(dict(records[0], body=body.replace("confirmed", "rejected")))
            value = [records]
        return SimpleNamespace(stdout=json.dumps(value))
    monkeypatch.setattr(subprocess, "run", fake)
    def invoke():
        path = tmp_path / "owner.json"
        if not state["absent"]:
            path.write_text(state["acceptance_raw"] if state["acceptance_raw"] is not None else json.dumps(owner_data))
        result = module["main"](["release", "--evidence", str(path), "--owner", "owner"])
        captured = capsys.readouterr()
        assert captured.err == "" and captured.out == ("RELEASE PASS\n" if result == 0 else "RELEASE FAIL: " + MESSAGE + "\n")
        return result
    return state, owner_data, invoke, native, provenance


@pytest.mark.parametrize("change", [None, "approved-en", "approved-zh", "recorded-later", "provenance-earlier"])
def test_ac_05_1_ac_05_3_ac_05_4_ctr03_ctr05_release_cli_accepts_owner_and_scoped_approval(acceptance, change):
    state, data, invoke, native, provenance = acceptance
    if change in ("approved-en", "approved-zh"):
        path = "rules/en.md" if change == "approved-en" else "examples/zh/a.yaml"
        for sha in (SHA, provenance): state["assets"][sha][path] = OTHER
    if change == "recorded-later": data["recorded_at"] = "2026-09-17T02:00:00Z"
    if change == "provenance-earlier": state["approval_time"] = "2026-09-17T00:00:00Z"
    assert invoke() == 0


@pytest.mark.parametrize("key,value", [("owner", "PRIVATE_DETAIL"), ("commit", OTHER), ("digest", "sha256:" + "d" * 64),
    ("tag", "v0.2.0"), ("run_attempt", 2), ("run_attempt", True), ("run_attempt", 1.0), ("extra", "PRIVATE_DETAIL"),
    ("recorded_at", "2026-09-17T00:59:59Z"), ("recorded_at", "2026-09-17T01:00:00+00:00"),
    ("native_pr_url", "https://evil.test/Owner/copyeditor/pull/7"),
    ("provenance_pr_url", "https://github.com/Other/repo/pull/8"),
    ("run_url", f"https://github.com/{REPO}/actions/runs/13/attempts/1"),
    ("run_url", f"https://github.com/{REPO}/actions/runs/12"),
    ("run_url", f"https://github.com/{REPO}/actions/runs/12/attempts/2")])
def test_ac_05_1_ac_05_3_ac_05_4_ctr03_ctr05_release_cli_rejects_owner_mismatch(acceptance, key, value):
    _, data, invoke, _, _ = acceptance
    data[key] = value
    assert invoke() == 1


@pytest.mark.parametrize("change", ["missing-file", "bad-json", "duplicate", "future-approval", "wrong-approver",
                                  "native-missing", "latest-refusal", "missing-artifact", "failed-attempt", "ja", "unapproved-en", "add", "delete"])
def test_ac_05_1_ac_05_3_ac_05_4_ctr03_ctr05_release_cli_rejects_unproven_acceptance(acceptance, change):
    state, data, invoke, native, provenance = acceptance
    if change == "missing-file": state["absent"] = True
    if change == "bad-json": state["acceptance_raw"] = "PRIVATE_DETAIL"
    if change == "duplicate": state["acceptance_raw"] = '{"owner":"owner","owner":"PRIVATE_DETAIL"}'
    if change == "future-approval": state["approval_time"] = "2026-09-17T01:00:01Z"
    if change == "wrong-approver": state["approval_owner"] = "someone"
    if change == "native-missing": state["native"] = False
    if change == "latest-refusal": state["refusal"] = True
    if change == "missing-artifact": state["missing"] = "artifact"
    if change == "failed-attempt":
        data.update(run_attempt=2, run_url=f"https://github.com/{REPO}/actions/runs/12/attempts/2")
    if change == "ja":
        for sha in (SHA, provenance): state["assets"][sha]["examples/ja/a.yaml"] = OTHER
    if change == "unapproved-en": state["assets"][SHA]["rules/en.md"] = OTHER
    if change == "add": state["assets"][SHA]["rules/nested/extra.txt"] = OTHER
    if change == "delete": del state["assets"][SHA]["examples/zh/a.yaml"]
    assert invoke() == 1


def test_ac_05_1_ac_05_3_ac_05_4_ctr03_ctr05_release_cli_requires_complete_strict_schema(acceptance):
    _, data, invoke, _, _ = acceptance
    for key in list(data):
        original = data.pop(key)
        assert invoke() == 1
        data[key] = None
        assert invoke() == 1
        data[key] = original
    for key in list(data["checks"]):
        for value in (False, 1, "true", None):
            data["checks"][key] = value
            assert invoke() == 1
        del data["checks"][key]
        assert invoke() == 1
        data["checks"][key] = True
    data["checks"]["extra"] = True
    assert invoke() == 1
