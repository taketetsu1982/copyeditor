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
from copyeditor.requests import edit_input_schema
from copyeditor.responses import tool_output_schema, validate_final
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
    def make(mode="none", flag=None, asynchronous=False, candidate=None):
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
                if candidate is not None:
                    items = [dict(item, text=candidate) for item in items]
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
            assert tool.input_schema == edit_input_schema(tool.name, config, snapshot)
            assert tool.output_schema == tool_output_schema(tool.name)
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


@pytest.mark.asyncio
@pytest.mark.consumer("CTR-01")
@pytest.mark.consumer("CTR-04")
@pytest.mark.parametrize("authenticated", [False, True])
async def test_ac_02_1_ac_02_5_ac_02_6_ac_02_8_ctr01_ctr04_raw_asgi(setup, authenticated):
    import httpx
    from fastmcp.server.auth import StaticTokenVerifier
    make, created, records = setup
    server, _, _ = make("google" if authenticated else "none")
    if authenticated:
        server.auth = StaticTokenVerifier(tokens={"test-token": {"client_id": "test", "scopes": [], "sub": "tester"}})
    app = server.http_app(path=None, json_response=True, stateless_http=True)
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    def call(arguments):
        return dict(jsonrpc="2.0", id=1, method="tools/call", params=dict(name="lint_text", arguments=arguments))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=headers) as client:
            assert (await client.get("/health")).json() == {"status": "ok"}
            invalid = [b'{"jsonrpc":"2.0","id":1,"method":"ping","method":"ping"}', b'{"x":"\xff"}',
                       json.dumps(dict(jsonrpc="2.0", id=1, method=MARKER)).encode(),
                       json.dumps(call({}) | {"params": {"name": MARKER}}).encode(), b'{}',
                       json.dumps(dict(jsonrpc="2.0", id="\ud800", method=MARKER)).encode()]
            if authenticated:
                for body in [*invalid, b" " * 262145, json.dumps(call([])).encode()]:
                    denied = await client.post("/mcp", content=body)
                    assert denied.status_code == 401 and "www-authenticate" in denied.headers
                    assert MARKER not in denied.text + str(denied.headers)
                assert not created and not records
                client.headers["authorization"] = "Bearer test-token"
            for body, code in zip(invalid, (-32700, -32700, -32601, -32602, -32600, -32600)):
                rejected = await client.post("/mcp", content=body)
                assert rejected.status_code == 400 and "error" in rejected.json() and "result" not in rejected.json()
                assert rejected.json()["error"]["code"] == code
                assert MARKER not in rejected.text + str(rejected.headers)
            assert not created and not records
            ping = b'{"jsonrpc":"2.0","id":1,"method":"ping"}'
            assert (await client.post("/mcp", content=ping + b" " * (262144 - len(ping)))).json()["result"] == {}
            assert (await client.post("/mcp", content=ping + b" " * (262145 - len(ping)))).status_code == 413
            for arguments in ([], None, MARKER, 1, True, {"text": MARKER, "extra": MARKER}, {"text": "Hello."}):
                response = await client.post("/mcp", json=call(arguments))
                assert response.status_code == 200
                result = response.json()["result"]
                payload = result["structuredContent"]
                validate_final(payload)
                assert json.loads(result["content"][0]["text"]) == payload
                assert result["isError"] == (arguments != {"text": "Hello."})
                assert MARKER not in response.text + str(response.headers)
            assert len(records) == 7 and not created and MARKER not in json.dumps(records)
            polished = await client.post("/mcp", json=call({"text": "Hello."}) | {"params": {"name": "polish_text", "arguments": {"text": "Hello."}}})
            assert polished.json()["result"]["structuredContent"]["text"] == "Hello."
            assert len(records) == 8 and len(created) == 1


@pytest.fixture
def tcp_server(setup):
    import asyncio
    from contextlib import asynccontextmanager
    import socket
    import threading
    import httpx
    import uvicorn
    from fastmcp.server.auth import StaticTokenVerifier
    from pydantic import AnyHttpUrl
    make, created, records = setup
    @asynccontextmanager
    async def start(authenticated, candidate=None):
        server, config, snapshot = make("google" if authenticated else "none", candidate=candidate)
        if authenticated:
            server.auth = StaticTokenVerifier(tokens={"tcp-token": {"client_id": "test", "scopes": [], "sub": MARKER}})
            server.auth.resource_base_url = AnyHttpUrl("https://service.example")
        listener = socket.socket()
        thread, runner = None, None
        try:
            listener.bind(("127.0.0.1", 0))
            address = listener.getsockname()
            runner = uvicorn.Server(uvicorn.Config(server.http_app(json_response=True, stateless_http=True),
                log_config=None, access_log=False, timeout_graceful_shutdown=2))
            thread = threading.Thread(target=runner.run, kwargs={"sockets": [listener]}, daemon=True)
            thread.start()
            async with asyncio.timeout(5):
                while not runner.started:
                    assert thread.is_alive(), "Server exited before startup"
                    await asyncio.sleep(.02)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{address[1]}", trust_env=False, timeout=5,
                    headers={"accept": "application/json, text/event-stream", "content-type": "application/json"}) as client:
                yield client, config, snapshot, created, records
        finally:
            if runner:
                runner.should_exit = True
            if thread:
                await asyncio.to_thread(thread.join, 5)
                if thread.is_alive():
                    runner.force_exit = True
                    await asyncio.to_thread(thread.join, 5)
            listener.close()
            assert thread is None or not thread.is_alive()
            assert listener.fileno() == -1
            if runner and runner.started:
                with socket.socket() as probe:
                    probe.settimeout(.2)
                    assert probe.connect_ex(address) != 0, "Listener survived cleanup"
    return start


@pytest.mark.asyncio
@pytest.mark.consumer("CTR-01")
@pytest.mark.consumer("CTR-04")
@pytest.mark.parametrize("authenticated", [False, True])
async def test_ac_02_1_ac_02_5_ac_02_6_ac_02_8_ctr01_ctr04_tcp_acceptance(tcp_server, authenticated):
    def rpc(method, **params):
        return dict(jsonrpc="2.0", id=1, method=method, params=params)
    async def chunks(body):
        for offset in range(0, len(body), 997):
            yield body[offset:offset + 997]
    async with tcp_server(authenticated) as (client, config, snapshot, created, records):
        health = await client.get("/health")
        assert health.status_code == 200 and health.json() == {"status": "ok"}
        negatives = [(b'{"jsonrpc":"2.0","id":1,"method":"ping","method":"ping"}', -32700),
                     (b'{"x":"\xff"}', -32700), (b'{', -32700), (b'{}', -32600),
                     (json.dumps(rpc(MARKER)).encode(), -32601),
                     (json.dumps(rpc("tools/call", name=MARKER, arguments={})).encode(), -32602)]
        if authenticated:
            for token in (None, MARKER):
                if token:
                    client.headers["authorization"] = "Bearer " + token
                for body in [*[b for b, _ in negatives], b" " * 262145,
                             json.dumps(rpc("tools/call", name="polish_text", arguments=[])).encode()]:
                    response = await client.post("/mcp", content=chunks(body))
                    assert response.status_code == 401
                    assert 'resource_metadata="https://service.example/.well-known/oauth-protected-resource/mcp"' in response.headers["www-authenticate"]
                    assert MARKER not in response.text + str(response.headers)
            assert not created and not records
            client.headers["authorization"] = "Bearer tcp-token"
        for body, code in negatives:
            response = await client.post("/mcp", content=chunks(body))
            assert response.status_code == 400 and response.json()["error"]["code"] == code
            assert "result" not in response.json() and MARKER not in response.text + str(response.headers)
        ping = json.dumps(rpc("ping", padding="界" * 60000), ensure_ascii=False).encode("utf-8")
        for size in (262143, 262144, 262145):
            body = ping + b" " * (size - len(ping))
            for chunked in (False, True):
                response = await client.post("/mcp", content=chunks(body) if chunked else body)
                assert response.status_code == (413 if size > 262144 else 200)
                if size <= 262144:
                    assert response.json()["result"] == {}
                assert MARKER not in response.text + str(response.headers)
        initialized = await client.post("/mcp", json=rpc("initialize", protocolVersion="2025-11-25", capabilities={},
                                                            clientInfo={"name": "tcp-test", "version": "1"}))
        assert initialized.json()["result"]["instructions"] == INSTRUCTIONS
        listed = await client.post("/mcp", json=rpc("tools/list"))
        tools = listed.json()["result"]["tools"]
        assert {t["name"] for t in tools} == {"polish_text", "lint_text"}
        for tool in tools:
            assert tool["inputSchema"] == edit_input_schema(tool["name"], config, snapshot)
            assert tool["outputSchema"] == tool_output_schema(tool["name"])
            assert tool["annotations"]["readOnlyHint"] and not tool["annotations"]["destructiveHint"]
            assert tool["annotations"]["openWorldHint"] == (tool["name"] == "polish_text")
        assert not created and not records
        for tool in ("polish_text", "lint_text"):
            for arguments in ([], None, 1, True, MARKER, {"text": MARKER, "extra": MARKER}, {"text": "Hello."}):
                before = len(records)
                response = await client.post("/mcp", json=rpc("tools/call", name=tool, arguments=arguments))
                result = response.json()["result"]
                payload = result["structuredContent"]
                validate_final(payload)
                assert response.status_code == 200 and len(result["content"]) == 1
                assert result["content"][0]["text"] == json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                failed = arguments != {"text": "Hello."}
                assert result["isError"] == failed and (payload["status"] == "error") == failed
                if failed:
                    assert payload["error"]["code"] == "invalid_input" and payload["model_calls"] == 0
                assert len(records) == before + 1 and set(records[-1]) == FIELDS
                assert records[-1]["tool"] == tool and records[-1]["status"] == ("error" if failed else "ok")
                assert MARKER not in response.text + str(response.headers) + json.dumps(records)
        assert len(records) == 14 and len(created) == 1
        assert all(bool(r["user"]) == authenticated for r in records)


def input_boundaries():
    yield "items", {"items": [{"id": "a", "text": "Hello."}]}, "en", None
    yield "both", {"text": "Hello.", "items": [{"id": "a", "text": "Hello."}]}, "en", "invalid_input"
    yield "neither", {}, "en", "invalid_input"
    yield "explicit", {"text": "Hello.", "language": "ja"}, "ja", None
    yield "unsupported", {"text": "Hello.", "language": "zz"}, None, "unsupported_language"
    for offset in (-1, 0, 1):
        error = "input_limit" if offset > 0 else None
        yield f"items-{offset}", {"items": [{"id": f"i{i}", "text": "Hello."} for i in range(32 + offset)]}, "en", error
        yield f"body-{offset}", {"text": "x" * (12000 + offset)}, "en", error
        yield f"items-body-total-{offset}", {"items": [{"id": "a", "text": "x" * 6000},
                                                      {"id": "b", "text": "y" * (6000 + offset)}]}, "en", error
        background = {k: "b" * (1000 + (offset if k == "message" else 0)) for k in ("audience", "purpose", "tone", "message")}
        yield f"background-{offset}", dict(text="Hello.", **background), "en", error
        items = [{"id": f"i{i}", "text": "x" * 3000, "context": "c" * 1000} for i in range(3)]
        items.append({"id": "last", "text": "x" * 3000})
        items[0]["context"] = "c" * (1000 + min(offset, 0))
        items[-1]["context"] = "c" * max(offset, 0)
        yield f"combined-total-{offset}", dict(items=items, audience="b" * 1000), "en", error
        items = [{"id": f"i{i}", "text": "Hello.", "context": "c" * (1000 + (min(offset, 0) if i == 3 else 0))} for i in range(4)]
        items.append({"id": "last", "text": "Hello.", "context": "c" * max(offset, 0)})
        yield f"contexts-{offset}", dict(items=items), "en", error


@pytest.mark.asyncio
@pytest.mark.consumer("CTR-01")
@pytest.mark.parametrize("case,arguments,language,error", list(input_boundaries()), ids=lambda x: x if type(x) is str else None)
async def test_ac_02_1_ac_02_5_ac_02_6_ctr01_tcp_input_boundaries(tcp_server, case, arguments, language, error):
    async with tcp_server(False) as (client, config, snapshot, created, records):
        response = await client.post("/mcp", json=dict(jsonrpc="2.0", id=1, method="tools/call", params=dict(name="polish_text", arguments=arguments)))
        result = response.json()["result"]
        payload = result["structuredContent"]
        validate_final(payload)
        assert response.status_code == 200 and payload["language"] == language
        assert result["isError"] == bool(error) and payload.get("error", {}).get("code") == error
        assert payload["model_calls"] == len(created) == (0 if error else 1)
        if error:
            assert payload["model_called"] is False
        else:
            assert payload["status"] == "ok"
            if "items" in arguments:
                assert [item["id"] for item in payload["items"]] == [item["id"] for item in arguments["items"]]
        assert len(records) == 1 and records[0]["language"] == language and records[0]["error_code"] == error
        assert records[0]["model_calls"] == payload["model_calls"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["text", "items"])
async def test_ac_02_8_ctr01_tcp_success_never_logs_private_text(tcp_server, capsys, caplog, route):
    body, context, background, candidate = (MARKER + suffix for suffix in ("_BODY", "_CONTEXT", "_BACKGROUND", "_CANDIDATE"))
    arguments = {"text": body} if route == "text" else {"items": [{"id": "a", "text": body, "context": context}]}
    arguments.update({key: background + key for key in ("audience", "purpose", "tone", "message")})
    async with tcp_server(False, candidate=candidate) as (client, _, _, created, records):
        response = await client.post("/mcp", json=dict(jsonrpc="2.0", id=1, method="tools/call", params=dict(name="polish_text", arguments=arguments)))
        payload = response.json()["result"]["structuredContent"]
        item = payload if route == "text" else payload["items"][0]
        assert payload["status"] == "ok" and item["flag"] is None and item["text"] == candidate
        assert len(created) == len(records) == 1
        assert MARKER not in str(response.headers) + json.dumps(records)
    captured = repr(capsys.readouterr()) + repr(caplog.records)
    assert all(secret not in captured for secret in (body, context, background, candidate))
