import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
MARKER = "SYNTHETIC_PRIVATE_DETAIL"
FIELDS = {"timestamp", "user", "tool", "language", "rules_version", "model", "usage", "cost", "latency_ms",
          "status", "error_code", "model_calls", "regenerated", "rejected_count", "unfixable_count"}
SCRIPT = r'''
import asyncio, importlib.abc, json, logging, sys
from pathlib import Path
from types import SimpleNamespace
mode, stage, directory = sys.argv[1:]
directory = Path(directory)
marker = "SYNTHETIC_PRIVATE_DETAIL"
events = []
class ImportGuard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split(".")[0] in {"fastmcp", "google", "mcp", "uvicorn"}:
            assert logging.root.manager.disable == logging.CRITICAL
            logging.critical(marker)
sys.meta_path.insert(0, ImportGuard())
from copyeditor import __main__ as entry
from copyeditor import rules, providers, auth, server
from copyeditor.providers.base import GenerationResult, Usage
from fastmcp import Client
# Any attempted outbound socket connection fails inside this isolated process.
sys.addaudithook(lambda event, args: (_ for _ in ()).throw(AssertionError(marker)) if event == "socket.connect" else None)
def observe(name, original):
    def call(*args, **kwargs):
        events.append(name)
        logging.critical(marker)
        if stage == name and name == "auth":
            raise RuntimeError(marker)
        return original(*args, **kwargs)
    return call
entry.load_config = observe("config", entry.load_config)
rules.load_rules = observe("rules", rules.load_rules)
auth.make_auth = observe("auth", auth.make_auth)
class Provider:
    async def estimate_input(self, value): return 0
    async def generate(self, value):
        logging.critical(marker)
        return GenerationResult(json.dumps({"items": [dict(id=i.id, text=i.text, flag=None, diagnosis=None) for i in value.items]}),
                                "stop", Usage(1, 2, 3))
    async def aclose(self):
        events.append("close")
def prepare(config):
    events.append("provider")
    if stage == "adc":
        import google.auth
        google.auth.default = lambda **kwargs: (_ for _ in ()).throw(RuntimeError(marker))
        return original_provider(config)
    return Provider()
original_provider = providers.create_provider
providers.create_provider = prepare
server.get_access_token = lambda: SimpleNamespace(claims={"sub": marker})
async def bind(self, **kwargs):
    events.append("bind")
    assert kwargs == dict(host="127.0.0.1", port=8080, path="/mcp", show_banner=False,
                          uvicorn_config={"log_config": None, "access_log": False})
    assert (self.auth is None) == (mode == "none")
    async with Client(self) as client:
        for tool, arguments in [("lint_text", {"text": marker, "language": "en"}), ("polish_text", {"text": marker, "language": "en"}),
                                ("polish_text", {"text": marker, "unknown": marker})]:
            result = await client.call_tool(tool, arguments, raise_on_error=False)
            assert result.is_error == ("unknown" in arguments)
    if mode == "none":
        entry.warn_unauthenticated()
    logging.critical(marker)
server.PublicServer.run_http_async = bind
status = asyncio.run(entry.run(directory / "config.yaml", directory / "missing-rules" if stage == "rules" else Path("rules"), None))
(directory / "events.json").write_text(json.dumps(events))
raise SystemExit(status)
'''


@pytest.mark.parametrize("mode,stage,error,events", [
    ("none", "absent", None, ["config", "rules", "provider", "auth", "bind", "close"]),
    ("google", "absent", None, ["config", "rules", "provider", "auth", "bind", "close"]),
    ("none", "invalid", "invalid_config at config", ["config"]),
    ("none", "unreadable", "invalid_config at config", ["config"]),
    ("none", "rules", "invalid_rules at rules", ["config", "rules"]),
    ("none", "language", "invalid_config at config", ["config"]),
    ("none", "adc", "credentials_unavailable at credentials", ["config", "rules", "provider"]),
    ("google", "auth", "invalid_config at auth", ["config", "rules", "provider", "auth", "close"]),
])
def test_ac_05_2_ac_05_8_ac_05_9_ctr01_ctr04_startup(tmp_path, mode, stage, error, events):
    if stage == "invalid":
        (tmp_path / "config.yaml").write_text(f"[invalid: {MARKER}")
    if stage == "unreadable":
        (tmp_path / "config.yaml").mkdir()
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "SYSTEMROOT"}}
    env.update(PYTHONPATH=str(ROOT / "src"), GOOGLE_CLOUD_PROJECT="test", COPYEDITOR_HOST="127.0.0.1",
               COPYEDITOR_AUTH_MODE=mode,
               GOOGLE_OAUTH_CLIENT_ID="client", BASE_URL="https://service.example",
               COPYEDITOR_ALLOWED_DOMAINS='["example.com"]', GOOGLE_OAUTH_CLIENT_SECRET=MARKER,
               OAUTH_SIGNING_KEY=MARKER * 2)
    if stage == "language": env["COPYEDITOR_DEFAULT_LANGUAGE"] = "en"
    result = subprocess.run([sys.executable, "-c", SCRIPT, mode, stage, str(tmp_path)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert MARKER not in result.stdout + result.stderr
    assert result.returncode == int(error is not None), result.stderr
    assert json.loads((tmp_path / "events.json").read_text()) == events
    expected = f"ERROR: {error}.\n" if error else "WARNING: copyeditor is listening without authentication.\n" if mode == "none" else ""
    assert result.stderr == expected
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == (0 if error else 3)
    for index, record in enumerate(records):
        assert set(record) == FIELDS
        assert record["status"] == ("error" if index == 2 else "ok")
        assert record["tool"] == ("lint_text" if index == 0 else "polish_text")
        assert record["model_calls"] == (1 if index == 1 else 0)
        identity = hmac.new((MARKER * 2).encode(), ("copyeditor-audit:" + MARKER).encode(), hashlib.sha256).hexdigest()[:24]
        assert record["user"] == (None if mode == "none" else identity)
        assert json.dumps(record, ensure_ascii=False, separators=(",", ":")) in result.stdout.splitlines()


def test_ac_05_8_ac_05_9_ctr01_ctr04_writers_reject_unstructured_data(capsys):
    from copyeditor.audit import AuditEvent, startup_error, write_audit
    with pytest.raises(TypeError):
        write_audit({"body": MARKER})
    with pytest.raises(TypeError):
        AuditEvent(body=MARKER)
    startup_error(MARKER, MARKER)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "ERROR: invalid_config at config.\n"
