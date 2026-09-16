import base64
import copy
import json
from pathlib import Path
import runpy
import subprocess
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_evidence.py"
HEAD, BLOB, REVISION = "a" * 40, "b" * 40, "c" * 40
BLOBS = {"rules/ja.md": BLOB, "examples/ja/example.yaml": BLOB}


@pytest.fixture
def evidence(monkeypatch, capsys):
    module = runpy.run_path(str(SCRIPT))
    data = dict(head=HEAD, comparison_revision=REVISION, non_reuse="confirmed", blobs=BLOBS)
    def record(value=data, **changes):
        return dict(user={"login": "owner"}, body="```copyeditor-provenance-v1\n" + json.dumps(value) + "\n```",
                    updated_at="2026-09-17T00:00:00Z") | changes
    state = dict(comments=[record()], assets=dict(BLOBS), readme=module["ACK_EN"] + "\n" + module["ACK_JA"],
                 truncated=False, moved=False, comparison=REVISION, failure=False)
    calls = []
    def fake(command, **kwargs):
        assert command[:2] == ["gh", "api"] and command[3:5] == ["--method", "GET"]
        calls.append(command)
        if state["failure"]:
            raise OSError("PRIVATE_DETAIL")
        endpoint = command[2]
        if endpoint.endswith("/pulls/7"):
            result = {"user": {"login": "owner"}, "head": {"sha": "d" * 40 if state["moved"] and len(calls) > 1 else HEAD}}
        elif endpoint.startswith("repos/coji/natural-japanese/"):
            result = {"sha": state["comparison"]}
        elif "/git/commits/" in endpoint:
            result = {"tree": {"sha": HEAD}}
        elif "/git/trees/" in endpoint:
            result = dict(truncated=state["truncated"], tree=[dict(path=p, mode="100644", type="blob", sha=h)
                          for p, h in (state["assets"] | {"README.md": BLOB}).items()])
        elif "/git/blobs/" in endpoint:
            result = dict(encoding="base64", content=base64.b64encode(state["readme"].encode()).decode())
        else:
            assert endpoint.endswith("/issues/7/comments?per_page=100")
            assert command[5:] == ["--paginate", "--slurp"]
            result = [[], state["comments"]]
        return SimpleNamespace(stdout=json.dumps(result))
    monkeypatch.setattr(subprocess, "run", fake)
    def invoke():
        calls.clear()
        code = module["main"](["provenance", "--pr", "7", "--owner", "owner"])
        output = capsys.readouterr().out
        assert "PRIVATE_DETAIL" not in output and all("/actions/" not in c[2] for c in calls)
        return code, output
    return state, copy.deepcopy(data), record, invoke


def test_ac_04_7_owner_evidence_passes_without_published_run(evidence):
    state, data, record, invoke = evidence
    state["comments"] = [record(user={"login": "OWNER"})]
    assert invoke() == (0, "PROVENANCE PASS\n")


@pytest.mark.parametrize("change", ["head", "hash", "missing", "extra", "owner", "revision", "refusal", "absent",
                                    "ack_en", "ack_ja", "tree", "moved", "unavailable", "failure", "new_asset"])
def test_ac_04_7_invalid_provenance_fails(evidence, change):
    state, data, record, invoke = evidence
    if change == "head": data["head"] = "d" * 40
    if change == "hash": data["blobs"]["rules/ja.md"] = "d" * 40
    if change == "missing": data["blobs"].pop("rules/ja.md")
    if change == "extra": data["blobs"]["rules/extra.md"] = BLOB
    if change == "revision": data["comparison_revision"] = "main"
    if change == "refusal": data["non_reuse"] = "rejected"
    state["comments"] = [record(data)]
    if change == "owner": state["comments"][0]["user"]["login"] = "someone"
    if change == "absent": state["comments"] = []
    if change.startswith("ack_"): state["readme"] = state["readme"].split("\n")[change == "ack_en"]
    if change == "tree": state["truncated"] = True
    if change == "moved": state["moved"] = True
    if change == "unavailable": state["comparison"] = "d" * 40
    if change == "failure": state["failure"] = True
    if change == "new_asset": state["assets"]["examples/en/new.yaml"] = BLOB
    assert invoke()[0] == 1


@pytest.mark.parametrize("when", ["2026-09-17T00:00:00Z", "2026-09-17T01:00:00Z"])
def test_ac_04_7_latest_or_tied_refusal_overrides_approval(evidence, when):
    state, data, record, invoke = evidence
    data["non_reuse"] = "rejected"
    state["comments"].append(record(data, updated_at=when))
    assert invoke()[0] == 1
