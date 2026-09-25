"""The public MCP boundary and privacy-preserving request log."""
import hashlib
import hmac
import inspect
import json
import time
from datetime import datetime, timezone

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from fastmcp.tools import Tool, ToolResult
from mcp.types import TextContent, ToolAnnotations

from .auth_boundary import disable_library_logging
from .providers.vertex import MODEL_ERROR, ProviderFailure

INPUT_ERROR = "Provide nonblank text of 1 to 12,000 Unicode code points. Split longer text."
INPUT_SCHEMA = {"type": "object", "properties": {"text": {"type": "string", "minLength": 1,
                "maxLength": 12000}}, "required": ["text"], "additionalProperties": False}


def write_audit(record):
    print(json.dumps(record, ensure_ascii=True, separators=(",", ":")), flush=True)


def build_server(config, provider, auth=None, audit_sink=None):
    disable_library_logging()
    sink = audit_sink if audit_sink is not None else write_audit

    class PolishTool(Tool):
        async def run(self, arguments):
            started = time.monotonic()
            original = arguments.get("text") if isinstance(arguments, dict) else None
            record = dict(timestamp=datetime.now(timezone.utc).isoformat(), result="model_error",
                          input_chars=len(original) if isinstance(original, str) else 0, output_chars=0,
                          changed=None, model=config["model"], latency_ms=0, retries=0, usage={}, user=None)
            message, failed = MODEL_ERROR, True
            try:
                if config["auth.mode"] == "google":
                    token = get_access_token()
                    sub = token.claims.get("sub") if token else None
                    if type(sub) is not str or not sub:
                        record["result"], message = "auth_error", "Authentication failed."
                        return ToolResult(content=[TextContent(type="text", text=message)], is_error=True)
                    record["user"] = hmac.new(config.secrets["OAUTH_SIGNING_KEY"].encode(),
                        ("copyeditor-audit:" + sub).encode(), hashlib.sha256).hexdigest()[:24]
                valid = (type(arguments) is dict and set(arguments) == {"text"}
                         and type(original) is str and 1 <= len(original) <= 12000 and bool(original.strip()))
                if valid:
                    try:
                        original.encode("utf-8")
                    except UnicodeError:
                        valid = False
                if not valid:
                    record["result"], message = "input_error", INPUT_ERROR
                else:
                    result = await provider.polish(original)
                    message, failed = result.text, False
                    record.update(result="success", output_chars=len(message), changed=message != original,
                                  retries=result.retries, usage=result.usage)
            except ProviderFailure as error:
                record["retries"] = error.retries
            except Exception:
                pass
            finally:
                record["latency_ms"] = round((time.monotonic() - started) * 1000)
                written = sink(record)
                if inspect.isawaitable(written):
                    await written
            return ToolResult(content=[TextContent(type="text", text=message)], is_error=failed)

    server = FastMCP("copyeditor", version="0.4.0", auth=auth, mask_error_details=True,
        instructions="Send Japanese text to polish_text. It sends the body to Vertex AI in the configured "
                     "location (global by default). Compare the returned text with the original before using it. "
                     "On errors, keep the original. The server does not check preservation of meaning.")
    server.add_tool(PolishTool(name="polish_text", parameters=INPUT_SCHEMA,
        description="Polish Japanese text with Gemini; return only the rewritten body. No change if unnecessary.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)))

    @server.custom_route("/health", methods=["GET"])
    async def health(request):
        from starlette.responses import JSONResponse
        return JSONResponse({"status": "ok"})

    return server
