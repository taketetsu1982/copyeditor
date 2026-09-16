import json
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_evidence.py"
HEAD = "a" * 40
OTHER = "b" * 40
BODY = "Head: " + HEAD + "\n" + "\n".join(
    section + ": approved" for section in
    ("Vocabulary", "Syntax", "Structure", "Translation artifacts", "Context weights"))


def record(**changes):
    return {"user": {"login": "owner"}, "body": BODY, "state": "COMMENTED",
            "commit_id": HEAD, "updated_at": "2026-09-16T10:00:00Z",
            "submitted_at": "2026-09-16T10:00:00Z", **changes}


@pytest.fixture
def cli(monkeypatch, capsys):
    def invoke(comments=None, reviews=None, failure=None, changed_head=False):
        calls = []
        def fake(command, **kwargs):
            calls.append(command)
            assert command[:2] == ["gh", "api"] and command[3:5] == ["--method", "GET"]
            assert kwargs == dict(capture_output=True, text=True, check=True, timeout=60)
            if failure:
                raise failure
            endpoint = command[2]
            if "/comments?" in endpoint or "/reviews?" in endpoint:
                assert command[5:] == ["--paginate", "--slurp"]
                data = comments if "/comments?" in endpoint else reviews
                payload = [[], data or []]
            else:
                assert endpoint == "repos/{owner}/{repo}/pulls/7"
                payload = {"user": {"login": "owner"},
                           "head": {"sha": OTHER if changed_head and len(calls) > 1 else HEAD}}
            return SimpleNamespace(stdout=json.dumps(payload))
        monkeypatch.setattr(subprocess, "run", fake)
        monkeypatch.setattr(sys, "argv", [str(SCRIPT), "native", "--pr", "7", "--owner", "owner"])
        with pytest.raises(SystemExit) as result:
            runpy.run_path(str(SCRIPT), run_name="__main__")
        output = capsys.readouterr().out
        assert "private@example.test" not in output
        return result.value.code, output, calls
    return invoke


@pytest.mark.parametrize("review", [False, True])
def test_ac_04_1_owner_author_comment_or_commented_review(cli, review):
    code, output, calls = cli(reviews=[record()] if review else [], comments=[] if review else [record()])
    assert (code, output) == (0, "NATIVE PASS\n")
    assert len(calls) == 4


@pytest.mark.parametrize("change", [
    {"user": {"login": "someone-else"}}, {"body": BODY.replace(HEAD, OTHER)},
    {"body": BODY.replace("Syntax: approved", "")},
    {"body": BODY.replace("Syntax: approved", "Syntax: rejected")},
    {"body": BODY.replace("Syntax: approved", "Syntax: unconfirmed")},
    {"body": BODY + "\nSyntax: rejected"}, {"body": BODY + "\nHead: " + OTHER},
    {"updated_at": "invalid"}, {"updated_at": "2026-09-16T10:00:00"},
])
def test_ac_04_1_invalid_owner_evidence_fails(cli, change):
    assert cli(comments=[record(**change)])[0] == 1


@pytest.mark.parametrize("state,commit", [("COMMENTED", OTHER), ("APPROVED", HEAD), ("PENDING", HEAD)])
def test_ac_04_1_review_requires_commented_and_matching_commit(cli, state, commit):
    assert cli(reviews=[record(state=state, commit_id=commit)])[0] == 1


@pytest.mark.parametrize("verdict", ["rejected", "unconfirmed", ""])
@pytest.mark.parametrize("later", ["2026-09-16T11:00:00Z", "2026-09-16T10:00:00Z"])
def test_ac_04_1_latest_owner_record_overrides_older_approval(cli, verdict, later):
    negative = record(body=BODY.replace("Syntax: approved", "Syntax: " + verdict), updated_at=later)
    assert cli(comments=[negative], reviews=[record()])[0] == 1
    assert cli(comments=[record(), negative])[0] == 1


def test_ac_04_1_later_approval_supersedes_old_refusal(cli):
    refused = record(body=BODY.replace("Syntax: approved", "Syntax: rejected"))
    accepted = record(submitted_at="2026-09-16T12:00:00+01:00", commit_id=None)
    assert cli(comments=[refused], reviews=[accepted])[0] == 0


@pytest.mark.parametrize("failure", [OSError("private@example.test"),
    subprocess.CalledProcessError(1, "gh", stderr="private@example.test"),
    subprocess.TimeoutExpired("gh", 60), ValueError("private@example.test")])
def test_ac_04_1_query_failure_is_private_and_fail_closed(cli, failure):
    assert cli(failure=failure)[:2] == (1, "NATIVE FAIL: GitHub evidence could not be read or validated.\n")


def test_ac_04_1_head_change_and_absent_evidence_fail(cli):
    assert cli(comments=[record()], changed_head=True)[0] == 1
    assert cli()[0] == 1


def test_ac_04_1_login_case_and_crlf_are_accepted(cli):
    assert cli(comments=[record(user={"login": "OWNER"}, body=BODY.replace("\n", "\r\n"))])[0] == 0


@pytest.mark.parametrize("argv", [["native", "--pr", "0", "--owner", "owner"],
    ["native", "--pr", "7", "--owner", "bad/login"]])
def test_ac_04_1_invalid_cli_arguments_make_no_query(monkeypatch, argv):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid arguments must not query GitHub")
    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(SCRIPT))["main"](argv)
    assert result.value.code == 2
