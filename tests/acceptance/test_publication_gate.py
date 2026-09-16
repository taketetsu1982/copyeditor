import base64
import json
from pathlib import Path
import runpy
import subprocess
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_evidence.py"
NATIVE, PROVENANCE, TARGET, BLOB, NEW, README, COMPARISON = [c * 40 for c in "abcdef0"]
ASSETS = {"rules/ja.md": BLOB, "examples/ja/one.yaml": BLOB,
          "rules/en.md": BLOB, "examples/zh/one.yaml": BLOB}


@pytest.fixture
def gate(monkeypatch, capsys):
    module = runpy.run_path(str(SCRIPT))
    state = dict(assets={h: dict(ASSETS) for h in (NATIVE, PROVENANCE, TARGET)},
                 owner="owner", native=True, provenance=True, moved=False, failure=None,
                 wrong_commit=False, stale=False, truncated=False, mode="100644", blob=BLOB, duplicate=False, visited=False)
    calls = []
    def fake(command, **kwargs):
        assert command[:2] == ["gh", "api"] and command[3:5] == ["--method", "GET"]
        calls.append(command)
        if state["failure"]: raise state["failure"]
        endpoint = command[2]
        if "/pulls/" in endpoint and not endpoint.endswith("/reviews?per_page=100"):
            head = NATIVE if endpoint.endswith("/7") else PROVENANCE
            result = dict(user={"login": "owner"}, head={"sha": NEW if state["moved"] and state["visited"] else head})
        elif "/git/commits/" in endpoint:
            head = endpoint.rsplit("/", 1)[1]
            result = dict(sha=NEW if state["wrong_commit"] else head, tree={"sha": head})
        elif "/git/trees/" in endpoint:
            head = endpoint.rsplit("/", 1)[1].split("?")[0]
            state["visited"] |= head == TARGET
            entries = [dict(path=p, type="blob", mode="100644", sha=h)
                       for p, h in (state["assets"][head] | {"README.md": README}).items()]
            if head == TARGET:
                entries[0].update(mode=state["mode"], sha=state["blob"])
                if state["duplicate"]: entries.append(entries[0])
            result = dict(truncated=state["truncated"], tree=entries)
        elif "/git/blobs/" in endpoint:
            result = dict(encoding="base64", content=base64.b64encode((module["ACK_EN"] + module["ACK_JA"]).encode()).decode())
        else:
            records = []
            if "/issues/7/" in endpoint and state["native"]:
                body = "Head: " + NATIVE + "\n" + "\n".join(s + ": approved" for s in module["SECTIONS"])
                records = [dict(user={"login": state["owner"]}, body=body, updated_at="2026-09-17T00:00:00Z")]
            if "/issues/8/" in endpoint and state["provenance"]:
                data = dict(head=PROVENANCE, comparison_revision=COMPARISON, non_reuse="confirmed", blobs=state["assets"][PROVENANCE])
                if state["stale"]: data["blobs"] = {}
                records = [dict(user={"login": state["owner"]}, updated_at="2026-09-17T00:00:00Z",
                                body="```copyeditor-provenance-v1\n" + json.dumps(data) + "\n```")]
            result = [records]
        return SimpleNamespace(stdout=json.dumps(result))
    monkeypatch.setattr(subprocess, "run", fake)
    def invoke(extra=()):
        argv = ["publish-gate", "--native-pr", "7", "--provenance-pr", "8", "--owner", "owner", "--commit", TARGET]
        result = module["main"](argv + list(extra))
        output = capsys.readouterr().out
        assert "PRIVATE_DETAIL" not in output
        assert "copyeditor-provenance-v1" not in output
        return result, output
    return state, invoke, calls


@pytest.mark.parametrize("updated", [False, True])
def test_ac_05_1_ac_05_7_ctr04_distinct_commits_and_language_scopes(gate, updated):
    state, invoke, calls = gate
    if updated:
        for head in (PROVENANCE, TARGET):
            state["assets"][head].update({"rules/en.md": NEW, "examples/zh/one.yaml": NEW})
    assert invoke() == (0, "PUBLISH-GATE PASS\n")
    assert calls


@pytest.mark.parametrize("change", ["native", "provenance", "owner", "ja", "example", "en", "add", "delete",
                                    "empty", "truncated", "mode", "blob", "duplicate", "moved", "network", "auth", "malformed", "wrong_commit", "stale"])
def test_ac_05_1_ac_05_7_ctr04_publication_gate_rejects_before_release(gate, change):
    state, invoke, calls = gate
    if change in ("native", "provenance"): state[change] = False
    if change == "owner": state["owner"] = "someone-else"
    if change == "ja": state["blob"] = NEW
    if change == "example": state["assets"][TARGET]["examples/ja/one.yaml"] = NEW
    if change == "en": state["assets"][TARGET]["rules/en.md"] = NEW
    if change == "add": state["assets"][TARGET]["examples/en/new.yaml"] = NEW
    if change == "delete": del state["assets"][TARGET]["rules/en.md"]
    if change == "empty": state["assets"][TARGET] = {}
    if change in ("truncated", "duplicate", "moved", "wrong_commit", "stale"): state[change] = True
    if change == "mode": state["mode"] = "120000"
    if change == "blob": state["blob"] = "invalid"
    if change == "network": state["failure"] = OSError("PRIVATE_DETAIL")
    if change == "auth": state["failure"] = subprocess.CalledProcessError(1, "gh", stderr="PRIVATE_DETAIL")
    if change == "malformed": state["failure"] = ValueError("PRIVATE_DETAIL")
    assert invoke()[0] == 1


@pytest.mark.parametrize("extra", [("--commit", "main"), ("--native-pr", "0"), ("--provenance-pr", "-1"),
                                  ("--owner", "bad/login"), ("--pr", "7")])
def test_ac_05_1_ac_05_7_ctr04_invalid_publication_arguments_make_no_query(gate, extra):
    state, invoke, calls = gate
    with pytest.raises(SystemExit) as error: invoke(extra)
    assert error.value.code == 2 and not calls


@pytest.mark.parametrize("path", ["rules/ja.md", "examples/ja/one.yaml"])
def test_ac_05_1_ac_05_7_ctr04_new_provenance_cannot_replace_native_approval(gate, path):
    state, invoke, calls = gate
    for head in (PROVENANCE, TARGET): state["assets"][head][path] = NEW
    if path == "rules/ja.md": state["blob"] = NEW
    assert invoke()[0] == 1
