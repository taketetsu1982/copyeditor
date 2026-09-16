import hashlib
import hmac
import inspect
import json
from datetime import datetime, timezone

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.tools import Tool, ToolResult
from mcp.types import TextContent, ToolAnnotations

from .auth import disable_library_logging
from .requests import MESSAGES, input_schema
from .responses import output_schema

INSTRUCTIONS = (
    "copyeditor sends polish_text body, context and background to this server and Vertex AI. "
    "This server does not persist them or candidates. Compare meaning and preservation before applying local edits. "
    "Use either text or items [{id,text,context?}], never both; html uses text only. "
    "Set language explicitly when known; otherwise the server default applies. lint_text accepts text and language "
    "and calls no model. Keep originals on errors and flags. Provider retention follows its own policy."
)


def build_server(config, snapshot, service, auth, audit_sink):
    disable_library_logging()

    class PublicTool(Tool):
        async def run(self, arguments):
            user = None
            if config["auth.mode"] == "google":
                token = get_access_token()
                sub = token.claims.get("sub") if token else None
                if type(sub) is not str or not sub:
                    raise ToolError("Authentication failed.")
                user = hmac.new(config.secrets["OAUTH_SIGNING_KEY"].encode(),
                                ("copyeditor-audit:" + sub).encode(), hashlib.sha256).hexdigest()[:24]
            language = arguments.get("language", config["default_language"])
            payload = dict(status="error", schema_version=1, error=dict(code="internal_error", message=MESSAGES["internal_error"], field=None),
                           language=language if type(language) is str and language in snapshot.languages else None,
                           rules_version=snapshot.rules_version, common_version=snapshot.common_version,
                           model=config["model"] if self.name == "polish_text" else None,
                           usage=dict(input_tokens=0, output_tokens=0, total_tokens=0), cost=None, latency_ms=0,
                           model_calls=0, model_called=False, regeneration_attempted=False)
            try:
                try:
                    payload = await getattr(service, "polish" if self.name == "polish_text" else "lint")(arguments)
                except Exception:
                    pass
                encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                return ToolResult(content=[TextContent(type="text", text=encoded)], structured_content=payload,
                                  is_error=payload["status"] == "error")
            finally:
                failed = payload["status"] == "error"
                items = [] if failed else payload.get("items", [payload] if "text" in payload else [])
                kinds = [item["flag"]["kind"] for item in items if item["flag"]]
                record = {key: payload[key] for key in ("language", "rules_version", "model", "usage", "cost", "latency_ms", "model_calls")}
                record.update(timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), user=user,
                              tool=self.name, status="error" if failed else "flagged" if kinds else "ok",
                              error_code=payload["error"]["code"] if failed else None,
                              regenerated=payload["regeneration_attempted"] if failed else any(item["regenerated"] for item in items),
                              rejected_count=kinds.count("rejected"), unfixable_count=kinds.count("unfixable"))
                result = audit_sink(record)
                if inspect.isawaitable(result):
                    await result

    server = PublicServer("copyeditor", instructions=INSTRUCTIONS, auth=auth, mask_error_details=True)
    for name in ("polish_text", "lint_text"):
        server.add_tool(PublicTool(name=name, parameters=input_schema(name, config, snapshot), output_schema=output_schema(name),
                                  annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=name == "polish_text")))
    @server.custom_route("/health", methods=["GET"])
    async def health(request):
        from starlette.responses import JSONResponse
        return JSONResponse({"status": "ok"})
    return server


class RawBoundary:
    def __init__(self, app, path, auth):
        from fastmcp.server.http import RequireAuthMiddleware, build_resource_metadata_url
        self.app, self.path = app, path
        self.post = self.receive_post
        if auth:
            resource = auth._get_resource_url(path)
            self.post = RequireAuthMiddleware(self.post, auth.required_scopes,
                build_resource_metadata_url(resource) if resource else None, auth.challenge_scopes)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == self.path and scope["method"] == "POST":
            await self.post(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    async def receive_post(self, scope, receive, send):
        from mcp import types
        from pydantic import TypeAdapter
        from starlette.responses import JSONResponse, Response
        from typing import get_args
        from .responses import unique_object, reject_constant
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            chunk = event.get("body", b"")
            if len(body) + len(chunk) > 262144:
                await Response(status_code=413)(scope, receive, send)
                return
            body.extend(chunk)
            if not event.get("more_body", False):
                break
        identity, code, message = None, -32700, "Parse error"
        try:
            data = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
            code, message = -32600, "Invalid Request"
            TypeAdapter(types.JSONRPCMessage).validate_python(data, strict=True)
            if "method" in data and "id" in data:
                if type(data["id"]) is str:
                    data["id"].encode("utf-8")
                identity = data["id"]
                methods = {c.model_fields["method"].default for c in get_args(types.ClientRequest)}
                if data["method"] not in methods:
                    code, message = -32601, "Method not found"
                    raise ValueError()
                if data["method"] == "tools/call":
                    params = data.get("params")
                    if not isinstance(params, dict) or type(params.get("name")) is not str:
                        raise ValueError()
                    if params["name"] not in ("polish_text", "lint_text"):
                        code, message = -32602, "Unknown tool"
                        raise ValueError()
                    # The SDK rejects non-dicts before Tool.run; an invalid dict preserves service/audit handling.
                    if "arguments" in params and type(params["arguments"]) is not dict:
                        params["arguments"] = {"_invalid_arguments": True}
                code, message = -32602, "Invalid params"
                TypeAdapter(types.ClientRequest).validate_python(data, strict=True)
            encoded = json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        except (ValueError, TypeError, RecursionError):
            await JSONResponse(dict(jsonrpc="2.0", id=identity, error=dict(code=code, message=message)),
                               status_code=400)(scope, receive, send)
            return
        replayed = False
        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return dict(type="http.request", body=encoded, more_body=False)
        scope = dict(scope, headers=[(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"]
                     + [(b"content-length", str(len(encoded)).encode())])
        await self.app(scope, replay, send)


class PublicServer(FastMCP):
    def http_app(self, path="/mcp", middleware=None, **kwargs):
        from starlette.middleware import Middleware
        path = path or "/mcp"
        return super().http_app(path=path, middleware=[Middleware(RawBoundary, path=path, auth=self.auth),
                                                       *(middleware or [])], **kwargs)
