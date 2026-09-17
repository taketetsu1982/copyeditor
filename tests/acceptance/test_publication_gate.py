import json
from pathlib import Path
import runpy
import subprocess
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_evidence.py"
NATIVE, TARGET, BLOB, NEW, README = [c * 40 for c in "acdef"]
ASSETS = {"rules/ja.md": BLOB, "examples/ja/one.yaml": BLOB,
          "rules/en.md": BLOB, "examples/zh/one.yaml": BLOB}


@pytest.fixture
def gate(monkeypatch, capsys):
    module = runpy.run_path(str(SCRIPT))
    state = dict(assets={h: dict(ASSETS) for h in (NATIVE, TARGET)},
                 owner="owner", native=True, moved=False, failure=None,
                 wrong_commit=False, truncated=False, mode="100644", blob=BLOB, duplicate=False, visited=False)
    calls = []
    def fake(command, **kwargs):
        assert command[:2] == ["gh", "api"] and command[3:5] == ["--method", "GET"]
        calls.append(command)
        if state["failure"]: raise state["failure"]
        endpoint = command[2]
        assert "/8" not in endpoint and "repos/coji/" not in endpoint
        if "/pulls/" in endpoint and not endpoint.endswith("/reviews?per_page=100"):
            assert endpoint.endswith("/7")
            head = NATIVE
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
        else:
            records = []
            if "/issues/7/" in endpoint and state["native"]:
                body = "Head: " + NATIVE + "\n" + "\n".join(s + ": approved" for s in module["SECTIONS"])
                records = [dict(user={"login": state["owner"]}, body=body, updated_at="2026-09-17T00:00:00Z")]
            result = [records]
        return SimpleNamespace(stdout=json.dumps(result))
    monkeypatch.setattr(subprocess, "run", fake)
    def invoke(extra=()):
        argv = ["publish-gate", "--native-pr", "7", "--owner", "owner", "--commit", TARGET]
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
        state["assets"][TARGET].update({"rules/en.md": NEW, "examples/zh/one.yaml": NEW})
    assert invoke() == (0, "PUBLISH-GATE PASS\n")
    assert calls


@pytest.mark.parametrize("change", ["native", "owner", "ja", "example", "add", "delete",
                                    "empty", "truncated", "mode", "blob", "duplicate", "moved", "network", "auth", "malformed", "wrong_commit"])
def test_ac_05_1_ac_05_7_ctr04_publication_gate_rejects_before_release(gate, change):
    state, invoke, calls = gate
    if change == "native": state[change] = False
    if change == "owner": state["owner"] = "someone-else"
    if change == "ja": state["blob"] = NEW
    if change == "example": state["assets"][TARGET]["examples/ja/one.yaml"] = NEW
    if change == "add": state["assets"][TARGET]["examples/ja/new.yaml"] = NEW
    if change == "delete": del state["assets"][TARGET]["examples/ja/one.yaml"]
    if change == "empty": state["assets"][TARGET] = {}
    if change in ("truncated", "duplicate", "moved", "wrong_commit"): state[change] = True
    if change == "mode": state["mode"] = "120000"
    if change == "blob": state["blob"] = "invalid"
    if change == "network": state["failure"] = OSError("PRIVATE_DETAIL")
    if change == "auth": state["failure"] = subprocess.CalledProcessError(1, "gh", stderr="PRIVATE_DETAIL")
    if change == "malformed": state["failure"] = ValueError("PRIVATE_DETAIL")
    assert invoke()[0] == 1


@pytest.mark.parametrize("extra", [("--commit", "main"), ("--native-pr", "0"), ("--provenance-pr", "8"),
                                  ("--owner", "bad/login"), ("--pr", "7")])
def test_ac_05_1_ac_05_7_ctr04_invalid_publication_arguments_make_no_query(gate, extra):
    state, invoke, calls = gate
    with pytest.raises(SystemExit) as error: invoke(extra)
    assert error.value.code == 2 and not calls


@pytest.mark.parametrize("path", ["rules/ja.md", "examples/ja/one.yaml"])
def test_ac_05_1_ac_05_7_ctr04_changed_japanese_assets_require_native_approval(gate, path):
    state, invoke, calls = gate
    state["assets"][TARGET][path] = NEW
    if path == "rules/ja.md": state["blob"] = NEW
    assert invoke()[0] == 1


@pytest.mark.parametrize("path", ["rules/nested/custom.md", "rules/extra.txt"])
@pytest.mark.parametrize("change", ["add", "modify", "delete"])
def test_ac_05_1_ac_05_7_ctr04_non_japanese_assets_do_not_require_approval(gate, path, change):
    state, invoke, calls = gate
    if change != "add": state["assets"][NATIVE][path] = BLOB
    if change != "delete": state["assets"][TARGET][path] = NEW
    assert invoke() == (0, "PUBLISH-GATE PASS\n")
