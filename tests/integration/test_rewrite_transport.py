import json
import logging
from pathlib import Path

import pytest
from fastmcp import Client
from jsonschema import Draft202012Validator

from copyeditor.config import load_config
from copyeditor.providers.base import GenerationResult, Usage
from copyeditor.responses import tool_output_schema
from copyeditor.rules import load_rules
from copyeditor.server import build_server
from copyeditor.service import Service
from tests.integration.test_transport import FIELDS, MARKER

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["default", "polish", "rewrite", "items", "invalid", "degree", "budget", "exception", "lint"])
async def test_ac_07_1_ac_07_3_ac_07_6_ac_07_10_ctr01_public_versions_and_private_audit(case, capsys, caplog):
    config = load_config(ROOT / "absent-config", {"GOOGLE_CLOUD_PROJECT": "test", "COPYEDITOR_DEFAULT_LANGUAGE": "en"})
    snapshot = load_rules(ROOT / "rules", None)
    calls, estimates, records = [], [], []
    class Provider:
        async def estimate_input(self, data):
            estimates.append(data)
            return 300000 if case == "budget" else 0
        async def generate(self, data):
            calls.append(data)
            if data.stage == "diagnose":
                value = dict(diagnoses=[dict(id=i.id, status="issue", expression=MARKER, reason=MARKER) for i in data.items])
            else:
                value = dict(items=[dict(id=i.id, text=i.text, flag=None) for i in reversed(data.items)])
            return GenerationResult(json.dumps(value), "stop", Usage(1, 2, 3))
    service = Service(config, snapshot, Provider)
    if case == "exception":
        async def broken(arguments):
            raise RuntimeError(MARKER)
        service.polish = broken
    arguments = dict(text=MARKER)
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
        version = 1 if case in ("default", "polish", "degree", "lint") else 2
        assert payload["schema_version"] == version
        assert (payload.get("degree") == "rewrite") == (version == 2)
        failures = dict(invalid="invalid_input", degree="invalid_input", lint="invalid_input",
                        budget="request_budget", exception="internal_error")
        assert result.is_error == (case in failures)
        assert result.content[0].text == json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if case in failures:
            assert payload["error"]["code"] == failures[case] and payload["model_calls"] == 0
            assert not payload["model_called"] and not calls and MARKER not in json.dumps(payload)
            assert len(estimates) == (case == "budget")
        else:
            entries = payload.get("items", [payload])
            assert all(item["text"] == MARKER for item in entries)
            if version == 2:
                assert all(item["diagnosis"]["reason"] == MARKER for item in entries)
                assert len(estimates) == len(calls) == (3 if case == "items" else 2)
            if case == "items":
                assert [item["id"] for item in entries] == [str(i) for i in range(5)]
        assert len(records) == 1 and set(records[0]) == FIELDS
        assert MARKER not in json.dumps(records) and MARKER not in repr(capsys.readouterr())
        assert MARKER not in repr(caplog.records)
    finally:
        logging.disable(disabled)
