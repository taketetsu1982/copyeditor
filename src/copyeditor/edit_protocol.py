"""Complete generation-4 shapes, not registered in public discovery yet."""
import json
from copy import deepcopy

from jsonschema import Draft202012Validator, validators

from .config import MODEL
from .responses import output_schema as legacy_schema, valid
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


def validate_shape(payload, *, tool="polish_text"):
    encoded = None
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    except (ValueError, TypeError, RecursionError, OverflowError):
        pass
    checker = Draft202012Validator.TYPE_CHECKER.redefine("integer", lambda _, value: type(value) is int)
    validator = validators.extend(Draft202012Validator, type_checker=checker)
    valid(encoded is not None and validator(output_schema(tool, limits=False)).is_valid(payload))
    return encoded
