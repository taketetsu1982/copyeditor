"""Generation-four HTTP boundaries; synthetic providers are not quality evidence."""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from copyeditor.config import ConfigError, load_config
from copyeditor.edit_protocol import output_schema, validate_final
from copyeditor.judgment_v2 import POLICY_ID
from copyeditor.providers.base import GenerationResult, ProviderFailure, SourceItem, Usage
from copyeditor.providers.typesafe import TypeSafe
from copyeditor.rules import load_rules
from copyeditor.server import build_server
from copyeditor.service import Service
from tests.contracts.harness import FIXTURE_THRESHOLDS, fixture_registry
from tests.integration.test_judgment_transport import call

ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def public_client(tmp_path, *, enabled=False, responses=None, estimate=0):
    events, wires, records = [], [], []
    env = {"GOOGLE_CLOUD_PROJECT": "fixture"}
    options = {}
    if enabled:
        env.update(COPYEDITOR_JUDGMENT_ENABLED="true", COPYEDITOR_JUDGMENT_THRESHOLDS_VERSION="synthetic",
                   TYPESAFE_API_KEY="synthetic")
        options.update(thresholds=FIXTURE_THRESHOLDS, pairs={(POLICY_ID, "synthetic")})
    config = load_config(tmp_path / "absent", env, **options)
    class Editor:
        async def estimate_input(self, value):
            events.append("estimate")
            return estimate
        async def generate(self, value):
            events.append("generate")
            if responses is not None:
                result = responses.pop(0)
                if isinstance(result, BaseException):
                    raise result
                return result
            return GenerationResult(json.dumps(dict(items=[dict(id=i.id, text=i.text.replace("First", "Next"),
                flag=None, diagnosis="Clearer expression." if value.stage == "rewrite" else None)
                for i in value.items])), "stop", Usage(0, 0, 0))
    def judge(request):
        data = json.loads(request.content)
        wires.append(data)
        checking = "originals" in data["state"]
        events.append("verify" if checking else "detect")
        return httpx.Response(200, json=dict(model="jev-1.13.0", answers={key: dict(type="Noul",
            noul=0.3 if checking and key.endswith(".gate") else 0.9) for key in data["questions"]}))
    adapter = TypeSafe(config.secrets["TYPESAFE_API_KEY"], transport=httpx.MockTransport(judge)) if enabled else None
    snapshot = load_rules(ROOT / "rules", None)
    service = Service(config, snapshot, Editor, adapter, **({"registry": fixture_registry} if enabled else {}))
    server = build_server(config, snapshot, service, None, records.append)
    app = server.http_app(json_response=True, stateless_http=True)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost",
                    headers={"accept": "application/json, text/event-stream"}) as client:
                yield client, events, wires, records
    finally:
        if adapter:
            await adapter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("degree", ["polish", "rewrite"])
async def test_public_modes_share_discovery_and_complete_correlated_responses(tmp_path, enabled, degree):
    async with public_client(tmp_path, enabled=enabled) as (client, events, wires, records):
        discovery = await client.post("/mcp", json=dict(jsonrpc="2.0", id=2, method="tools/list", params={}))
        tools = discovery.json()["result"]["tools"]
        assert {t["name"] for t in tools} == {"polish_text", "lint_text"}
        for tool in tools:
            assert tool["outputSchema"] == output_schema(tool["name"])
        payload = await call(client, dict(text="First sentence.", language="en", degree=degree))
        validate_final(payload, (SourceItem("text", "First sentence.", ""),), expected_enabled=enabled,
                       registry=fixture_registry)
        assert payload["schema_version"] == 4 and payload["degree"] == degree and payload["status"] == "ok"
        assert payload["text"] == "Next sentence." and payload["providers"][0]["usage"]["total_tokens"] == 0
        before = list(events)
        lint = await call(client, dict(text="First sentence.", language="en"), "lint_text")
        assert lint["schema_version"] == 4 and events == before and len(records) == 2
        assert events == (["detect", "estimate", "generate", "verify"] if enabled else ["estimate", "generate"])
        assert len(wires) == (2 if enabled else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("failure,code", [
    (ProviderFailure("provider_error", Usage(None, None, None)), "provider_error"),
    (GenerationResult("{}", "truncated", Usage(1, 2, 3)), "generation_truncated"),
    (GenerationResult('{"items":[{"id":"text","text":"Next sentence.","flag":null,"diagnosis":null}]}',
                      "stop", Usage(1, 2, 3)), "invalid_response"),
])
async def test_failure_is_atomic_and_no_verification_or_retry_follows(tmp_path, enabled, failure, code):
    async with public_client(tmp_path, enabled=enabled, responses=[failure]) as (client, events, wires, records):
        result = await call(client, dict(text="First sentence.", language="en", degree="rewrite"))
        assert result["error"]["code"] == code and not {"text", "items", "diagnosis"} & result.keys()
        assert result["providers"][0]["usage"] == failure.usage._asdict()
        assert result["providers"][0]["model_calls"] == result["providers"][0]["estimation_calls"] == 1
        assert events == (["detect", "estimate", "generate"] if enabled else ["estimate", "generate"])
        assert len(wires) == int(enabled) and len(records) == 1
        await asyncio.sleep(0)
        assert "verify" not in events and events.count("generate") == 1


@pytest.mark.asyncio
async def test_budget_refusal_starts_no_generation_and_cancel_stops_all_followup(tmp_path):
    async with public_client(tmp_path, estimate=208897) as (client, events, _, _):
        payload = await call(client, dict(text="First sentence.", language="en", degree="rewrite"))
        assert payload["error"]["code"] == "request_budget" and events == ["estimate"]
        assert payload["providers"][0]["model_calls"] == 0
    async with public_client(tmp_path, enabled=True, responses=[asyncio.CancelledError()]) as (client, events, wires, _):
        response = await client.post("/mcp", json=dict(jsonrpc="2.0", id=1, method="tools/call",
            params=dict(name="polish_text", arguments=dict(text="First sentence.", language="en", degree="rewrite"))))
        assert response.json() == dict(jsonrpc="2.0", id=1, error=dict(code=-32000, message="Connection closed"))
        await asyncio.sleep(0)
        assert events == ["detect", "estimate", "generate"] and len(wires) == 1


@pytest.mark.parametrize("values", [{"COPYEDITOR_DEFAULT_LANGUAGE": "en"},
    {"COPYEDITOR_JUDGMENT_ENABLED": "true", "COPYEDITOR_JUDGMENT_THRESHOLDS_VERSION": "synthetic"}])
def test_retired_default_or_unregistered_production_judgment_cannot_resolve(tmp_path, values):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent", {"GOOGLE_CLOUD_PROJECT": "fixture", **values})


@pytest.mark.asyncio
@pytest.mark.parametrize("format,text", [("html", "<p>First sentence.</p>"), ("markdown", "# First sentence.")])
async def test_html_whole_document_and_markdown_candidates_keep_explicit_format(tmp_path, format, text):
    async with public_client(tmp_path) as (client, events, _, _):
        result = await call(client, dict(text=text, format=format, language="en"))
        assert result["status"] == "ok" and result["text"] == text.replace("First", "Next")
        assert events == ["estimate", "generate"]
    skill = (ROOT / "skills/copyeditor/SKILL.md").read_text()
    assert "Do not split or partially recover HTML" in skill
    assert "change to headings, lists, code, or link structure leaves that item for review" in skill


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unknown_version", "mixed_detection", "mixed_metadata"])
async def test_public_consumer_rejects_unknown_version_and_mixed_nulls(tmp_path, change):
    from copyeditor.requests import ValidationError
    async with public_client(tmp_path, enabled=True) as (client, _, _, _):
        payload = await call(client, dict(text="First sentence.", language="en"))
    if change == "unknown_version":
        payload["schema_version"] = 99
    elif change == "mixed_detection":
        payload["detection"] = None
    else:
        payload["policy_hash"] = None
    with pytest.raises(ValidationError):
        validate_final(payload, (SourceItem("text", "First sentence.", ""),), expected_enabled=True,
                       registry=fixture_registry)
