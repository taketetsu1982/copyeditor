import hashlib
import hmac
import json
import logging
import secrets
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.server.auth import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from copyeditor.config import load_config
from copyeditor.providers.base import GenerationResult, Usage
from copyeditor.requests import input_schema
from copyeditor.responses import output_schema, validate_final
from copyeditor.rules import load_rules
from copyeditor.server import INSTRUCTIONS, build_server
from copyeditor.service import Service

ROOT = Path(__file__).resolve().parents[2]
MARKER = "SYNTHETIC_PRIVATE_DETAIL"
FIELDS = {"timestamp", "user", "tool", "language", "rules_version", "model", "usage", "cost", "latency_ms", "status",
          "error_code", "model_calls", "regenerated", "rejected_count", "unfixable_count"}


@pytest.fixture
def setup(tmp_path, capsys, caplog):
    disabled = logging.root.manager.disable
    created, records = [], []
    def make(mode="none", flag=None, asynchronous=False):
        env = {"GOOGLE_CLOUD_PROJECT": "test", "COPYEDITOR_DEFAULT_LANGUAGE": "en"}
        if mode == "google":
            env.update(COPYEDITOR_AUTH_MODE="google", GOOGLE_OAUTH_CLIENT_ID="client", BASE_URL="https://service.example",
                       COPYEDITOR_ALLOWED_DOMAINS='["example.com"]', GOOGLE_OAUTH_CLIENT_SECRET=secrets.token_urlsafe(32),
                       OAUTH_SIGNING_KEY=secrets.token_urlsafe(32))
        config = load_config(tmp_path / "absent", env)
        snapshot = load_rules(ROOT / "rules", None)
        class Provider:
            async def generate(self, value):
                items = [dict(id=i.id, text=i.text if flag != "reject" else i.text.replace("10", "11"),
                              flag=dict(kind="unfixable", reason="Cannot edit.") if flag == "unfixable" else None) for i in value.items]
                return GenerationResult(json.dumps({"items": items}), "stop", Usage(1, 2, 3))
        def factory():
            created.append(True)
            return Provider()
        async def sink(record): records.append(record)
        server = build_server(config, snapshot, Service(config, snapshot, factory), None, sink if asynchronous else records.append)
        return server, config, snapshot
    yield make, created, records
    logging.disable(disabled)
    assert MARKER not in repr(capsys.readouterr()) and MARKER not in repr(caplog.records)


@pytest.mark.asyncio
async def test_ac_02_1_ac_02_5_ac_02_6_ac_02_8_ctr01_discovery(setup):
    make, _, records = setup
    server, config, snapshot = make()
    async with Client(server) as client:
        assert client.instructions == INSTRUCTIONS
        contract = (ROOT / "contracts/tools.md").read_text().split("## Initialization instructions")[1].split("```text\n")[1].split("\n```")[0]
        assert INSTRUCTIONS == contract
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == {"polish_text", "lint_text"}
        for tool in tools:
            assert tool.input_schema == input_schema(tool.name, config, snapshot)
            assert tool.output_schema == output_schema(tool.name)
            assert tool.annotations.read_only_hint and tool.annotations.destructive_hint is False
            assert tool.annotations.open_world_hint == (tool.name == "polish_text")
        with pytest.raises(Exception): await client.call_tool("unknown", {"text": MARKER})
    assert records == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["success", "lint", "invalid", "unfixable", "reject"])
async def test_ac_02_1_ac_02_5_ctr01_fixed_results_and_single_audit(setup, kind):
    make, created, records = setup
    server, _, _ = make(flag=kind, asynchronous=kind == "lint")
    tool = "lint_text" if kind == "lint" else "polish_text"
    args = {"text": "Pay 10."} if kind != "invalid" else {"text": MARKER, "unknown": MARKER}
    async with Client(server) as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    payload = result.structured_content
    validate_final(payload)
    assert len(result.content) == 1
    assert result.content[0].text == json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    assert result.is_error == (kind == "invalid")
    assert len(records) == 1 and set(records[0]) == FIELDS
    record = records[0]
    assert record["user"] is None and record["tool"] == tool
    assert record["status"] == ("error" if kind == "invalid" else "flagged" if kind in ("unfixable", "reject") else "ok")
    assert record["unfixable_count"] == (kind == "unfixable") and record["rejected_count"] == (kind == "reject")
    assert record["regenerated"] == (kind == "reject")
    assert len(created) == (kind not in ("invalid", "lint"))
    assert MARKER not in json.dumps(payload) and MARKER not in json.dumps(records)


@pytest.mark.asyncio
async def test_ctr04_ctr01_authenticated_audit_hmac(setup):
    make, created, records = setup
    server, config, _ = make("google")
    token = AccessToken(token=secrets.token_urlsafe(32), client_id="client", scopes=[], claims={"sub": MARKER, "email": MARKER})
    context = auth_context_var.set(AuthenticatedUser(token))
    try:
        async with Client(server) as client:
            result = await client.call_tool("lint_text", {"text": "Hello."})
        assert not result.is_error
    finally:
        auth_context_var.reset(context)
    assert len(records) == 1 and not created
    assert records[0]["user"] == hmac.new(config.secrets["OAUTH_SIGNING_KEY"].encode(), ("copyeditor-audit:" + MARKER).encode(), hashlib.sha256).hexdigest()[:24]
    assert MARKER not in json.dumps(records)
    async with Client(server) as client:
        denied = await client.call_tool("polish_text", {"text": MARKER}, raise_on_error=False)
    assert denied.is_error and len(records) == 1 and not created
    assert MARKER not in repr(denied)


@pytest.mark.asyncio
async def test_ctr01_unexpected_service_exception_is_fixed_and_audited(setup, monkeypatch):
    make, created, records = setup
    server, _, _ = make()
    async def fail(self, arguments): raise RuntimeError(MARKER)
    monkeypatch.setattr(Service, "polish", fail)
    async with Client(server) as client:
        result = await client.call_tool("polish_text", {"text": MARKER}, raise_on_error=False)
    assert result.is_error and result.structured_content["error"]["code"] == "internal_error"
    validate_final(result.structured_content)
    assert len(records) == 1 and records[0]["status"] == "error" and not created
    assert MARKER not in repr(result) and MARKER not in json.dumps(records)
