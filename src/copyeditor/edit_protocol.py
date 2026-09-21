"""Complete generation-4 shapes, not registered in public discovery yet."""
import json
from copy import deepcopy

from jsonschema import Draft202012Validator

from .config import MODEL
from .judgment_v2 import classify_detection, snapshot
from .requests import ValidationError
from .responses import output_schema as legacy_schema, validate_final as legacy_validate, valid
from .rewrite_response import BUDGET_MESSAGE


def output_schema(tool, *, limits=True):
    valid(tool in ("polish_text", "lint_text"))
    schema = legacy_schema(tool)
    variants = []
    def obj(**fields):
        return dict(type="object", properties=fields, required=list(fields), additionalProperties=False)
    def nullable(field):
        return {"anyOf": [field, {"type": "null"}]}
    integer = {"type": "integer", "minimum": 0}
    number = {"type": "number", "minimum": 0, "maximum": 1}
    detection = {"oneOf": [obj(status={"enum": ["eligible", "insufficient"]},
        reason={"const": "evaluated"}, probability=number), obj(status={"const": "not_run"},
        reason={"const": "no_editable_prose"}, probability={"type": "null"})]}
    for source in schema["oneOf"]:
        for enabled in ((False, True) if tool == "polish_text" else (False,)):
            variant = deepcopy(source)
            fields = variant["properties"]
            fields["schema_version"] = {"type": "integer", "const": 4}
            error = "error" in fields
            if error:
                errors = fields["error"]["oneOf"]
                errors[:] = [e for e in errors if e["properties"]["code"]["const"] != "html_structure"]
                if tool == "polish_text":
                    errors.append(obj(code={"const": "request_budget"}, message={"const": BUDGET_MESSAGE}, field={"type": "null"}))
                else:
                    errors[:] = [e for e in errors if e["properties"]["code"]["const"] in
                                 ("invalid_input", "unsupported_language", "input_limit", "output_limit", "internal_error")]
            if tool == "polish_text":
                row = {key: fields[key] for key in ("usage", "cost", "latency_ms")}
                rows = [obj(role={"const": role}, provider={"const": provider}, model=model,
                    model_calls={**integer, "maximum": maximum}, estimation_calls=integer if role == "editing" else {"const": 0, "type": "integer"},
                    **deepcopy(row)) for role, provider, model, maximum in (
                        ("editing", "vertex", {"type": "string", "pattern": "^(?:" + MODEL + ")(?![\\s\\S])"}, 16),
                        ("judgment", "typesafe", {"const": "jev-1.13.0"}, 64))][:2 if enabled else 1]
                for key in ("model", "usage", "model_calls"):
                    del fields[key]
                fields.update(judgment_enabled={"type": "boolean", "const": enabled},
                    degree={"enum": ["polish", "rewrite", None] if error else ["polish", "rewrite"]},
                    providers={"type": "array", "prefixItems": rows, "items": False, "minItems": len(rows), "maxItems": len(rows)})
                for key in ("policy_version", "thresholds_version", "policy_hash", "thresholds_hash"):
                    fields[key] = ({"type": "string", **({"pattern": "^[0-9a-f]{64}$"} if key.endswith("hash") else {"minLength": 1})}
                                   if enabled else {"type": "null"})
                if not error:
                    target = fields["items"]["items"] if "items" in fields else variant
                    props = target["properties"]
                    diagnosis = {"type": "string", "pattern": "^[^\\r\\n\\ud800-\\udfff]*$(?![\\s\\S])"}
                    if limits:
                        diagnosis["maxLength"] = 320
                    props.update(diagnosis=nullable(diagnosis), detection=detection if enabled else {"type": "null"})
                    if not limits:
                        props["text"].pop("maxLength", None)
                    target["required"] = list(props)
            variant["required"] = list(fields)
            variants.append(variant)
    schema["oneOf"] = variants
    return schema


def validate_final(payload, originals=(), *, tool="polish_text", expected_enabled=False, registry=snapshot, format="text"):
    encoded = None
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    except (ValueError, TypeError, RecursionError, OverflowError):
        pass
    valid(encoded is not None and Draft202012Validator(output_schema(tool, limits=False)).is_valid(payload))
    if tool == "lint_text":
        legacy_validate({**payload, "schema_version": 1})
        return
    enabled = payload["judgment_enabled"]
    valid(enabled == expected_enabled)
    selected = None
    if enabled:
        selected = registry(payload["policy_version"], payload["thresholds_version"])
        valid(payload["policy_hash"] == selected.policy_hash and payload["thresholds_hash"] == selected.threshold_hash)
    editing = payload["providers"][0]
    for row in payload["providers"]:
        if not row["model_calls"]:
            valid(all(v == 0 for v in row["usage"].values()) and row["cost"] is None)
        if not row["model_calls"] + row["estimation_calls"]:
            valid(row["latency_ms"] == 0)
        if any(row["usage"][key] is None for key in ("input_tokens", "output_tokens")):
            valid(row["cost"] is None)
    called = [row for row in payload["providers"] if row["model_calls"]]
    costs = [row["cost"] for row in called]
    if not costs or None in costs or len({cost["currency"] for cost in costs}) != 1:
        valid(payload["cost"] is None)
    else:
        valid(payload["cost"] is not None and payload["cost"]["currency"] == costs[0]["currency"])
    calls = editing["model_calls"]
    maximum = 16 if payload["degree"] == "rewrite" else 2
    valid(calls <= maximum and calls <= editing["estimation_calls"] <= maximum)
    if payload["status"] == "error":
        valid(payload["model_called"] == bool(called))
        valid(not payload["regeneration_attempted"] or calls >= 2)
        if payload["error"]["code"] in ("invalid_input", "unsupported_language", "input_limit"):
            valid(not called and editing["estimation_calls"] == 0 and not payload["regeneration_attempted"])
        entries = []
    else:
        from .responses import nonblank
        from .language_detection import _prose
        entries = payload.get("items", [payload])
        valid(len(entries) == len(originals) and bool(originals) and len({o.id for o in originals}) == len(originals))
        if "items" in payload:
            valid([item["id"] for item in entries] == [o.id for o in originals])
        generated = 0
        for item, original in zip(entries, originals):
            detection, diagnosis = item["detection"], item["diagnosis"]
            if enabled and detection["status"] != "not_run":
                valid(detection == classify_detection(detection["probability"], selected))
            if enabled and detection["status"] == "not_run":
                valid(format == "html" and not nonblank(_prose(original.text)))
            admitted = not enabled or detection["status"] == "eligible"
            generated += admitted
            valid(not enabled or not admitted or item["flag"] or item["text"] != original.text or item["regenerated"])
            if not admitted:
                valid(item["text"] == original.text and item["flag"] is None and not item["regenerated"] and diagnosis is None)
            valid(nonblank(diagnosis) if admitted and payload["degree"] == "rewrite" else diagnosis is None)
            if item["flag"] and item["flag"]["kind"] == "unfixable":
                valid(item["text"] == original.text)
        valid(editing["estimation_calls"] == calls)
        if enabled:
            evaluated = sum(item["detection"]["status"] != "not_run" for item in entries)
            count = payload["providers"][1]["model_calls"]
            valid(1 <= count <= evaluated + 2 * generated if evaluated else count == 0)
        retried = any(item["regenerated"] for item in entries)
        batches = (generated + 3) // 4 if payload["degree"] == "rewrite" else int(bool(generated))
        valid(batches <= calls <= 2 * batches and retried == (calls > batches))
        # Reuse unchanged findings/preservation integrity, not historical provider equations.
        common = legacy_schema("polish_text")["oneOf"][0]["properties"]
        projection = {key: deepcopy(value) for key, value in payload.items() if key in common}
        projection.update(schema_version=1, model=editing["model"], usage=dict.fromkeys(("input_tokens", "output_tokens", "total_tokens"), 0), cost=None, model_calls=2 if retried else 1)
        projected = [{key: deepcopy(value) for key, value in item.items() if key in common or key == "id"} for item in entries]
        if "items" in payload:
            projection["items"] = projected
        else:
            projection.update(projected[0])
            projection["schema_version"] = 1
        legacy_validate(projection, check_limits=False)
    if (sum(len(item["text"]) for item in entries) > 16000 or len(encoded) > 1048576
            or any(len(item["diagnosis"] or "") > 320 for item in entries)
            or sum(len(item["diagnosis"] or "") for item in entries) > 8192):
        raise ValidationError("output_limit", None)
