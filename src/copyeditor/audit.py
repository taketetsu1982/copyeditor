import json
import sys
from dataclasses import asdict, dataclass

from .config import SCHEMA


@dataclass(frozen=True)
class AuditEvent:
    timestamp: str
    user: str | None
    tool: str
    language: str | None
    rules_version: str
    model: str | None
    usage: dict
    cost: dict | None
    latency_ms: int
    status: str
    error_code: str | None
    model_calls: int
    regenerated: bool
    rejected_count: int
    unfixable_count: int


def write_audit(event: AuditEvent):
    if type(event) is not AuditEvent:
        raise TypeError("Expected an audit event.")
    record = asdict(event)
    record["usage"] = {key: event.usage[key] for key in ("input_tokens", "output_tokens", "total_tokens")}
    record["cost"] = None if event.cost is None else {key: event.cost[key] for key in ("amount", "currency")}
    print(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)


_warned = False


def warn_unauthenticated():
    global _warned
    if not _warned:
        print("WARNING: copyeditor is listening without authentication.", file=sys.stderr, flush=True)
        _warned = True


def startup_error(code: str, field: str):
    codes = {"invalid_config", "missing_required", "unsupported_provider", "invalid_rules", "credentials_unavailable"}
    fields = set(SCHEMA) | {key.split(".")[0] for key in SCHEMA} | {"config", "rules", "credentials"}
    if code not in codes or field not in fields:
        code, field = "invalid_config", "config"
    print(f"ERROR: {code} at {field}.", file=sys.stderr, flush=True)
