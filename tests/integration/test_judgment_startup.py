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
from copyeditor.judged_response import judged_output_schema
from copyeditor.judgment import ACTION_CRITERIA
from copyeditor.providers import typesafe
from copyeditor.providers.base import GenerationResult, Usage
from tests.integration.test_startup import FIELDS


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "on", "reject", "missing", "invalid", "construct", "bind"])
async def test_startup_wires_public_calls_and_closes_clients(tmp_path, monkeypatch, capsys, mode):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(dict(vertex=dict(project="fixture"), judgment=dict(enabled=mode != "off"))))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    if mode != "missing": monkeypatch.setenv("TYPESAFE_API_KEY", "bad key" if mode == "invalid" else "synthetic")
    events, records, wires = [], [], []
    class Editor:
        async def estimate_input(self, value): return 0
        async def generate(self, value):
            events.append(value.stage)
            body = (dict(diagnoses=[dict(id=i.id, status="issue", expression=i.text, reason="Expression issue.") for i in value.items])
                    if value.stage == "diagnose" else dict(items=[dict(id=i.id, text=i.text, flag=None) for i in value.items]))
            return GenerationResult(json.dumps(body), "stop", Usage(1, 1, 2))
        async def aclose(self): events.append("editor_closed")
    monkeypatch.setattr(providers, "create_provider", lambda config: Editor())
    original = typesafe.TypeSafe
    def handler(request):
        data = json.loads(request.content)
        wires.append(data)
        answers = {key: (dict(type="choice", choice="simplify_vocabulary", confidence=0,
                    probabilities={a: int(a == "simplify_vocabulary") for a in ACTION_CRITERIA})
                    if q["type"] == "choice" else dict(type="noul", noul=0.1 if mode == "reject" and "pairs" in data["state"] else 0.9)) for key, q in data["questions"].items()}
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
            instructions = Path("contracts/tools.md").read_text().split("## Initialization instructions")[1].split("## Audit log")[0]
            assert client.instructions in instructions and len(client.instructions) < 512
            assert ("TypeSafe AI" in client.instructions) == (mode in ("on", "reject"))
            assert f"copyeditor.judgment={'on' if mode in ('on', 'reject') else 'off'};" in tools["polish_text"].description
            for degree in ("polish", "rewrite", "invalid"):
                result = await client.call_tool("polish_text", dict(text="Example.", degree=degree), raise_on_error=False)
                payload = result.structured_content
                assert result.is_error == (degree == "invalid")
                if mode in ("on", "reject"):
                    assert tools["polish_text"].output_schema == judged_output_schema()
                    Draft202012Validator(judged_output_schema()).validate(payload)
                    assert payload["degree"] == (None if degree == "invalid" else degree)
                    assert payload["providers"][0]["model_calls"] == (0 if degree == "invalid" else 2 if degree == "rewrite" else 1)
                else: assert payload["schema_version"] == (2 if degree == "rewrite" else 1)
            result = await client.call_tool("lint_text", dict(text="Example."))
            assert result.structured_content["schema_version"] == 1
        assert all(set(record.__dict__) == FIELDS for record in records)
        assert [r.rejected_count for r in records] == ([1, 1, 0, 0] if mode == "reject" else [0, 0, 0, 0])
    monkeypatch.setattr(server.PublicServer, "run_http_async", bind)
    disabled = logging.root.manager.disable
    try:
        code = await entry.run(path, Path("rules"), None)
    finally:
        logging.disable(disabled)
    stderr = capsys.readouterr().err
    assert "PRIVATE" not in stderr and "synthetic" not in stderr
    assert code == (0 if mode in ("off", "on", "reject") else 1)
    assert ("bind" in events) == (mode in ("off", "on", "reject", "bind"))
    assert ("judgment_closed" in events) == (mode in ("on", "reject", "bind"))
    assert ("editor_closed" in events) == (mode not in ("missing", "invalid"))
    assert ("judgment is enabled" in stderr) == (mode in ("on", "reject", "bind"))
    assert len(wires) == (4 if mode in ("on", "reject") else 0)
    if mode in ("missing", "invalid", "construct"):
        code = {"missing": "missing_required", "invalid": "invalid_config", "construct": "credentials_unavailable"}[mode]
        assert stderr == f"ERROR: {code} at judgment.credentials.\n"


def test_disabled_public_loader_never_reads_judgment_secret(tmp_path):
    class Poison(dict):
        def get(self, key, default=None):
            assert key != "TYPESAFE_API_KEY"
            return super().get(key, default)
    assert not load_config(tmp_path / "absent", Poison(GOOGLE_CLOUD_PROJECT="test"))["judgment.enabled"]
