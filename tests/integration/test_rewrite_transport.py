"""Transport regression observations (partial AC evidence).
AC-08-1: single v4 behavior; AC-08-7: enabled failures remain complete v4 errors.
AC-08-9, AC-08-10: private audit fields and error-side model-call accounting."""
import json
import logging
from pathlib import Path

import pytest
from fastmcp import Client
from jsonschema import Draft202012Validator

from copyeditor.config import load_config
from copyeditor.providers.base import GenerationResult, Usage
from copyeditor.edit_protocol import output_schema as tool_output_schema
from copyeditor.rules import load_rules
from copyeditor.server import build_server
from copyeditor.service import Service
from tests.integration.test_transport import FIELDS, MARKER

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["default", "polish", "rewrite", "items", "invalid", "degree", "budget", "exception", "lint"])
async def test_ac_07_1_ac_07_3_ac_07_6_ac_07_10_ctr01_public_versions_and_private_audit(case, capsys, caplog):
    config = load_config(ROOT / "absent-config", {"GOOGLE_CLOUD_PROJECT": "test"})
    snapshot = load_rules(ROOT / "rules", None)
    calls, estimates, records = [], [], []
    class Provider:
        async def estimate_input(self, data):
            estimates.append(data)
            return 300000 if case == "budget" else 0
        async def generate(self, data):
            calls.append(data)
            body = dict(items=[dict(id=i.id, text=i.text, flag=None,
                diagnosis="No expression change needed." if data.stage == "rewrite" else None) for i in reversed(data.items)])
            return GenerationResult(json.dumps(body), "stop", Usage(1, 2, 3))
    service = Service(config, snapshot, Provider)
    if case == "exception":
        async def broken(arguments):
            raise RuntimeError(MARKER)
        service.polish = broken
    arguments = dict(text=MARKER, language="en")
    if case != "default":
        arguments["degree"] = "polish" if case == "polish" else "invalid" if case == "degree" else "rewrite"
    if case == "items":
        arguments.pop("text")
        arguments["items"] = [dict(id=str(i), text=MARKER) for i in range(5)]
    if case == "invalid":
        arguments["unknown"] = MARKER
    tool = "lint_text" if case == "lint" else "polish_text"
    disabled = logging.root.manager.disable
    try:
        server = build_server(config, snapshot, service, None, records.append)
        async with Client(server) as client:
            result = await client.call_tool(tool, arguments, raise_on_error=False)
        payload = result.structured_content
        Draft202012Validator(tool_output_schema(tool)).validate(payload)
        version = 4
        assert payload["schema_version"] == version
        assert payload.get("degree") == (None if case in ("degree", "lint") else "polish" if case in ("default", "polish") else "rewrite")
        failures = dict(invalid="invalid_input", degree="invalid_input", lint="invalid_input",
                        budget="request_budget", exception="internal_error")
        assert result.is_error == (case in failures)
        assert result.content[0].text == json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if case in failures:
            assert payload["error"]["code"] == failures[case] and (payload["providers"][0]["model_calls"] if tool == "polish_text" else payload["model_calls"]) == 0
            assert not payload["model_called"] and not calls and MARKER not in json.dumps(payload)
            assert len(estimates) == (case == "budget")
        else:
            entries = payload.get("items", [payload])
            assert all(item["text"] == MARKER for item in entries)
            assert all(item["diagnosis"] == ("No expression change needed." if payload["degree"] == "rewrite" else None) for item in entries)
            assert len(estimates) == len(calls) == (2 if case == "items" else 1)
            if case == "items":
                assert [item["id"] for item in entries] == [str(i) for i in range(5)]
        assert len(records) == 1 and set(records[0]) == FIELDS
        assert MARKER not in json.dumps(records) and MARKER not in repr(capsys.readouterr())
        assert MARKER not in repr(caplog.records)
    finally:
        logging.disable(disabled)


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", [False, True])
async def test_enabled_provider_and_unexpected_service_failures_are_v4(broken):
    import httpx
    from tests.contracts.harness import FIXTURE_THRESHOLDS, fixture_registry
    from copyeditor.judgment_v2 import POLICY_ID
    from copyeditor.judgment import JudgmentFailure
    from copyeditor.edit_protocol import output_schema
    config = load_config(ROOT / "absent-config", {"GOOGLE_CLOUD_PROJECT": "test",
        "COPYEDITOR_JUDGMENT_ENABLED": "true", "TYPESAFE_API_KEY": "synthetic",
        "COPYEDITOR_JUDGMENT_THRESHOLDS_VERSION": "synthetic"}, thresholds=FIXTURE_THRESHOLDS, pairs={(POLICY_ID, "synthetic")})
    class Judgment:
        async def evaluate(self, wire): return JudgmentFailure("provider_error", Usage(None, None, None))
    service = Service(config, load_rules(ROOT / "rules", None), lambda: pytest.fail("Unexpected editor"), Judgment(), registry=fixture_registry)
    if broken:
        async def fail(arguments): raise RuntimeError(MARKER)
        service.polish = fail
    records, disabled = [], logging.root.manager.disable
    try:
        server = build_server(config, service.snapshot, service, None, records.append)
        async with Client(server) as client:
            result = await client.call_tool("polish_text", dict(text=MARKER, degree="rewrite", language="en"), raise_on_error=False)
        payload = result.structured_content
        Draft202012Validator(output_schema("polish_text")).validate(payload)
        assert result.is_error and payload["error"]["code"] == ("internal_error" if broken else "provider_error")
        assert payload["model_called"] == (not broken) and payload["degree"] == "rewrite"
        assert set(records[0]) == FIELDS and records[0]["model_calls"] == 0
        assert MARKER not in repr(payload) + repr(records)
        app = server.http_app(json_response=True, stateless_http=True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost",
                                        headers={"accept": "application/json, text/event-stream"}) as client:
                for arguments in (None, [], True, 1, MARKER):
                    response = await client.post("/mcp", json=dict(jsonrpc="2.0", id=1, method="tools/call",
                        params=dict(name="polish_text", arguments=arguments)))
                    payload = response.json()["result"]["structuredContent"]
                    Draft202012Validator(output_schema("polish_text")).validate(payload)
                    assert payload["degree"] is None and not payload["model_called"]
                    assert payload["error"]["code"] == ("internal_error" if broken else "invalid_input")
        assert len(records) == 6 and all(set(record) == FIELDS for record in records)
    finally:
        logging.disable(disabled)
