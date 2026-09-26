import json
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client

from copyeditor.config import load_config
from copyeditor.providers.vertex import Generation, ProviderFailure
from copyeditor.server import build_server


async def invoke(arguments, output="result", failure=None):
    provider = AsyncMock()
    provider.polish.return_value = Generation(output, retries=1, usage={"total_tokens": 12})
    provider.polish.side_effect = failure
    records = []
    server = build_server(load_config({"GOOGLE_CLOUD_PROJECT": "test"}), provider, audit_sink=records.append)
    async with Client(server) as client:
        result = await client.call_tool("polish_text", arguments, raise_on_error=False)
    return result, provider, records


@pytest.mark.asyncio
async def test_discovery_has_only_text_tool_with_closed_schema():
    server = build_server(load_config({"GOOGLE_CLOUD_PROJECT": "test"}), AsyncMock(), audit_sink=lambda record: None)
    async with Client(server) as client:
        tools = await client.list_tools()
    assert [tool.name for tool in tools] == ["polish_text"]
    tool = tools[0]
    assert set(tool.input_schema["properties"]) == {"text"}
    assert tool.input_schema["required"] == ["text"]
    assert tool.input_schema["additionalProperties"] is False
    assert tool.input_schema["properties"]["text"]["minLength"] == 1
    assert tool.input_schema["properties"]["text"]["maxLength"] == 12000
    assert tool.output_schema is None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.open_world_hint is True


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["x", "\U0001f600" * 12000, "e\u0301" * 6000, "  body\n"])
async def test_valid_codepoint_boundaries_and_unchanged_output(body):
    result, provider, records = await invoke({"text": body}, output=body)
    assert not result.is_error and result.structured_content is None
    assert len(result.content) == 1 and result.content[0].type == "text"
    assert result.content[0].text == body
    provider.polish.assert_awaited_once_with(body)
    assert len(records) == 1
    assert records[0]["input_chars"] == records[0]["output_chars"] == len(body)
    assert records[0]["changed"] is False and records[0]["result"] == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{}, {"text": ""}, {"text": " \n\t\u3000"}, {"text": "\U0001f600" * 12001}, {"text": None}, {"text": 1}, {"text": ["PRIVATE_BODY"]}, {"text": "PRIVATE_BODY", "degree": "rewrite"}])
async def test_invalid_arguments_are_safe_tool_errors_without_generation(arguments):
    result, provider, records = await invoke(arguments)
    assert result.is_error and result.structured_content is None
    assert len(result.content) == 1
    assert "PRIVATE_BODY" not in result.content[0].text
    provider.polish.assert_not_awaited()
    assert len(records) == 1 and records[0]["result"] == "input_error"
    assert "PRIVATE_BODY" not in json.dumps(records)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ProviderFailure(retries=2), RuntimeError("PRIVATE_EXCEPTION")])
async def test_model_errors_have_fixed_message_and_safe_audit(failure, capsys, caplog):
    result, provider, records = await invoke({"text": "PRIVATE_BODY"}, failure=failure)
    assert result.is_error and len(result.content) == 1
    assert result.structured_content is None
    assert "PRIVATE" not in result.content[0].text
    assert len(records) == 1 and records[0]["result"] == "model_error"
    assert records[0]["output_chars"] == 0
    assert records[0]["retries"] == (2 if isinstance(failure, ProviderFailure) else 0)
    assert "PRIVATE" not in json.dumps(records) + repr(capsys.readouterr()) + caplog.text


@pytest.mark.asyncio
async def test_success_audit_contains_counts_without_body(capsys):
    result, provider, records = await invoke({"text": "PRIVATE_BODY"}, output="PRIVATE_OUTPUT")
    assert not result.is_error
    record = records[0]
    assert record["input_chars"] == 12 and record["output_chars"] == 14
    assert record["changed"] is True and record["retries"] == 1
    assert record["usage"] == {"total_tokens": 12}
    assert record["user"] is None and record["latency_ms"] >= 0
    assert record["timestamp"] and record["model"] == "gemini-3.7-flash"
    assert "PRIVATE" not in json.dumps(record) + repr(capsys.readouterr())


@pytest.mark.asyncio
async def test_default_sink_writes_one_safe_json_line(capsys):
    provider = AsyncMock()
    provider.polish.return_value = Generation("PRIVATE_OUTPUT")
    server = build_server(load_config({"GOOGLE_CLOUD_PROJECT": "test"}), provider)
    capsys.readouterr()
    async with Client(server) as client:
        result = await client.call_tool("polish_text", {"text": "PRIVATE_BODY"})
    assert not result.is_error
    output = capsys.readouterr()
    lines = output.out.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["result"] == "success"
    assert "PRIVATE" not in output.out + output.err


@pytest.mark.asyncio
async def test_authenticated_audit_uses_stable_distinct_pseudonyms(monkeypatch):
    import secrets
    from types import SimpleNamespace
    import copyeditor.server as module

    config = load_config({"GOOGLE_CLOUD_PROJECT": "test", "COPYEDITOR_AUTH_MODE": "google",
                          "BASE_URL": "https://service.example", "GOOGLE_OAUTH_CLIENT_ID": "client",
                          "COPYEDITOR_ALLOWED_DOMAINS": '["example.com"]',
                          "GOOGLE_OAUTH_CLIENT_SECRET": secrets.token_urlsafe(32),
                          "OAUTH_SIGNING_KEY": secrets.token_urlsafe(32)})
    provider = AsyncMock()
    provider.polish.return_value = Generation("\u672c\u6587")
    records = []
    server = build_server(config, provider, audit_sink=records.append)
    async with Client(server) as client:
        for subject in ("PRIVATE_SUBJECT", "PRIVATE_SUBJECT", "OTHER_PRIVATE_SUBJECT"):
            token = SimpleNamespace(claims={"sub": subject, "email": "PRIVATE@example.com"})
            monkeypatch.setattr(module, "get_access_token", lambda: token)
            result = await client.call_tool("polish_text", {"text": "\u672c\u6587"}, raise_on_error=False)
            assert not result.is_error
    users = [record["user"] for record in records]
    assert users[0] == users[1] and users[0] != users[2]
    assert all(len(user) == 24 and all(character in "0123456789abcdef" for character in user) for user in users)
    assert "PRIVATE" not in json.dumps(records)
    monkeypatch.setattr(module, "get_access_token", lambda: None)
    async with Client(server) as client:
        result = await client.call_tool("polish_text", {"text": "\u672c\u6587"}, raise_on_error=False)
    assert result.is_error and records[-1]["result"] == "auth_error"
    assert provider.polish.await_count == 3


@pytest.mark.asyncio
async def test_model_failure_audit_preserves_usage_without_private_details(capsys, caplog):
    usage = {"prompt_tokens": 17, "candidates_tokens": 0, "total_tokens": 23}
    result, provider, records = await invoke({"text": "PRIVATE_BODY"}, failure=ProviderFailure(retries=2, usage=usage))
    assert result.is_error and len(result.content) == 1 and result.structured_content is None
    assert len(records) == 1 and records[0]["result"] == "model_error"
    assert records[0]["usage"] == usage and records[0]["retries"] == 2
    assert "PRIVATE" not in result.content[0].text + json.dumps(records) + repr(capsys.readouterr()) + caplog.text
    provider.polish.assert_awaited_once_with("PRIVATE_BODY")
