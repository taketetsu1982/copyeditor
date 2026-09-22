"""Startup/public-call observations with fake providers.
AC-08-1, AC-08-7: disabled secret isolation and fail-closed startup.
AC-08-5, AC-08-9, AC-08-10: rejected counts, audit privacy and provider calls.
AC-08-11, AC-08-18: startup/tool/initialization disclosure, not client consent."""
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from jsonschema import Draft202012Validator

from copyeditor import __main__ as entry, providers, server
from copyeditor.config import load_config
from copyeditor.edit_protocol import output_schema
from copyeditor.judgment_v2 import POLICY_ID
from copyeditor import service
from tests.contracts.harness import FIXTURE_THRESHOLDS, fixture_registry
from copyeditor.providers import typesafe
from copyeditor.providers.base import GenerationResult, Usage
from tests.integration.test_startup import FIELDS


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "on", "reject", "missing", "invalid", "construct", "bind", "unregistered"])
async def test_startup_wires_public_calls_and_closes_clients(tmp_path, monkeypatch, capsys, mode):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(dict(vertex=dict(project="fixture"), judgment=dict(enabled=mode != "off", thresholds_version="synthetic" if mode != "off" else None))))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    if mode != "missing": monkeypatch.setenv("TYPESAFE_API_KEY", "bad key" if mode == "invalid" else "synthetic")
    resolver, implementation = entry.load_config, service.Service
    monkeypatch.setattr(entry, "load_config", lambda *args, **kwargs: resolver(*args, **kwargs,
        thresholds={} if mode == "unregistered" else FIXTURE_THRESHOLDS, pairs={(POLICY_ID, "synthetic")}))
    monkeypatch.setattr(service, "Service", lambda *args: implementation(*args, registry=fixture_registry))
    events, records, wires = [], [], []
    class Editor:
        async def estimate_input(self, value): return 0
        async def generate(self, value):
            events.append(value.stage)
            body = dict(items=[dict(id=i.id, text=i.text.replace("Example", "Sample").replace("10", "11" if mode == "reject" else "10"),
                flag=None, diagnosis="Clearer wording." if value.stage == "rewrite" else None) for i in value.items])
            return GenerationResult(json.dumps(body), "stop", Usage(1, 1, 2))
        async def aclose(self): events.append("editor_closed")
    monkeypatch.setattr(providers, "create_provider", lambda config: Editor())
    original = typesafe.TypeSafe
    def handler(request):
        data = json.loads(request.content)
        wires.append(data)
        answers = {key: dict(type="Noul", noul=0.3 if "originals" in data["state"] and key.endswith(".gate") else 0.9)
                   for key in data["questions"]}
        return httpx.Response(200, json=dict(model="jev-1.13.0", answers=answers, usage=dict(input_tokens=1, output_tokens=1)))
    def make(secret, **kwargs):
        assert mode != "off"
        if mode == "construct": raise RuntimeError("PRIVATE")
        client = original(secret, transport=httpx.MockTransport(handler), **kwargs)
        close = client.aclose
        async def tracked_close():
            events.append("judgment_closed")
            await close()
        client.aclose = tracked_close
        return client
    monkeypatch.setattr(typesafe, "TypeSafe", make)
    monkeypatch.setattr(entry, "write_audit", lambda record: records.append(record))
    async def bind(self, **kwargs):
        events.append("bind")
        if mode == "bind": raise RuntimeError("PRIVATE")
        async with Client(self) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            instructions = Path("contracts/tools.md").read_text().split("Initialization instructions (disabled:")[1].split("### Current consumer")[0]
            assert client.instructions in (instructions if mode != "off" else instructions.replace(" and TypeSafe AI", "")) and len(client.instructions) < 512
            assert ("TypeSafe AI" in client.instructions) == (mode in ("on", "reject"))
            assert f"copyeditor.judgment={'on' if mode in ('on', 'reject') else 'off'};" in tools["polish_text"].description
            for degree in ("polish", "rewrite", "invalid"):
                result = await client.call_tool("polish_text", dict(text="Example 10.", degree=degree, language="en"), raise_on_error=False)
                payload = result.structured_content
                assert result.is_error == (degree == "invalid")
                if mode in ("on", "reject"):
                    assert tools["polish_text"].output_schema == output_schema("polish_text")
                    Draft202012Validator(output_schema("polish_text")).validate(payload)
                    assert payload["degree"] == (None if degree == "invalid" else degree)
                    assert payload["providers"][0]["model_calls"] == (0 if degree == "invalid" else 2 if mode == "reject" else 1)
                else: assert payload["schema_version"] == 4
            result = await client.call_tool("lint_text", dict(text="Example 10.", language="en"))
            assert result.structured_content["schema_version"] == 4
        assert all(set(record.__dict__) == FIELDS for record in records)
        assert [r.rejected_count for r in records] == ([1, 1, 0, 0] if mode == "reject" else [0, 0, 0, 0])
    assertions = []
    async def checked_bind(self, **kwargs):
        try:
            return await bind(self, **kwargs)
        except AssertionError as error:
            assertions.append(error)
            raise
    monkeypatch.setattr(server.PublicServer, "run_http_async", checked_bind)
    disabled = logging.root.manager.disable
    try:
        code = await entry.run(path, Path("rules"), None)
    finally:
        logging.disable(disabled)
    if assertions: raise assertions[0]
    stderr = capsys.readouterr().err
    assert "PRIVATE" not in stderr and "synthetic" not in stderr
    assert code == (0 if mode in ("off", "on", "reject") else 1)
    assert ("bind" in events) == (mode in ("off", "on", "reject", "bind"))
    assert ("judgment_closed" in events) == (mode in ("on", "reject", "bind"))
    assert ("editor_closed" in events) == (mode not in ("missing", "invalid", "unregistered"))
    assert ("judgment is enabled" in stderr) == (mode in ("on", "reject", "bind"))
    assert len(wires) == (6 if mode == "reject" else 4 if mode == "on" else 0)
    if mode in ("missing", "invalid", "construct"):
        code = {"missing": "missing_required", "invalid": "invalid_config", "construct": "credentials_unavailable"}[mode]
        assert stderr == f"ERROR: {code} at judgment.credentials.\n"


def test_disabled_public_loader_never_reads_judgment_secret(tmp_path):
    class Poison(dict):
        def get(self, key, default=None):
            assert key != "TYPESAFE_API_KEY"
            return super().get(key, default)
    assert not load_config(tmp_path / "absent", Poison(GOOGLE_CLOUD_PROJECT="test"))["judgment.enabled"]
