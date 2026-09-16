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

    server = FastMCP("copyeditor", instructions=INSTRUCTIONS, auth=auth, mask_error_details=True)
    for name in ("polish_text", "lint_text"):
        server.add_tool(PublicTool(name=name, parameters=input_schema(name, config, snapshot), output_schema=output_schema(name),
                                  annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=name == "polish_text")))
    return server
