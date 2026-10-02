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

TEXT_LIMIT = 20000
READER_LIMIT = 500
INPUT_ERROR = ("Provide nonblank text of 1 to 20,000 Unicode code points and, if given, "
               "a nonblank reader of up to 500. Split longer text.")
INPUT_SCHEMA = {"type": "object", "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": TEXT_LIMIT},
                    "reader": {"type": "string", "minLength": 1, "maxLength": READER_LIMIT}},
                "required": ["text"], "additionalProperties": False}


def acceptable(value, limit):
    if type(value) is not str or not 1 <= len(value) <= limit or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def write_audit(record):
    print(json.dumps(record, ensure_ascii=True, separators=(",", ":")), flush=True)


def build_server(config, provider, auth=None, audit_sink=None):
    disable_library_logging()
    sink = audit_sink if audit_sink is not None else write_audit

    class PolishTool(Tool):
        async def run(self, arguments):
            started = time.monotonic()
            original = arguments.get("text") if isinstance(arguments, dict) else None
            reader = arguments.get("reader") if isinstance(arguments, dict) else None
            record = dict(timestamp=datetime.now(timezone.utc).isoformat(), result="model_error",
                          input_chars=len(original) if isinstance(original, str) else 0, output_chars=0,
                          changed=None, model=config["model"], reader=None, latency_ms=0, retries=0, usage={},
                          user=None)
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
                valid = (type(arguments) is dict and set(arguments) <= {"text", "reader"}
                         and acceptable(original, TEXT_LIMIT)
                         and ("reader" not in arguments or acceptable(reader, READER_LIMIT)))
                if not valid:
                    record["result"], message = "input_error", INPUT_ERROR
                else:
                    record["reader"] = "given" if "reader" in arguments else "default"
                    result = await provider.polish(original, reader)
                    message, failed = result.text, False
                    record.update(result="success", output_chars=len(message), changed=message != original,
                                  retries=result.retries, usage=result.usage)
            except ProviderFailure as error:
                record["retries"] = error.retries
                record["usage"] = error.usage
            except Exception:
                pass
            finally:
                record["latency_ms"] = round((time.monotonic() - started) * 1000)
                written = sink(record)
                if inspect.isawaitable(written):
                    await written
            return ToolResult(content=[TextContent(type="text", text=message)], is_error=failed)

    server = FastMCP("copyeditor", version="0.5.3", auth=auth, mask_error_details=True,
        instructions="Send a Japanese document to polish_text, with an optional reader description. It sends the "
                     "document to Vertex AI in the configured location (global by default). Compare the returned "
                     "document with the original before using it. On errors, keep the original. The server does "
                     "not check preservation of meaning, names, or numbers.")
    server.add_tool(PolishTool(name="polish_text", parameters=INPUT_SCHEMA,
        description="Rewrite a Japanese document (Markdown, HTML, or plain text) for the given reader with Gemini, "
                    "keeping names, sources, numbers, certainty, and meaning. Returns only the rewritten document.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)))

    @server.custom_route("/health", methods=["GET"])
    async def health(request):
        from starlette.responses import JSONResponse
        return JSONResponse({"status": "ok"})

    return server
