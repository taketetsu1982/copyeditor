import json
import math
import re
from types import MappingProxyType
from typing import NamedTuple

from .requests import ID, SPACE, ValidationError


class Candidate(NamedTuple):
    id: str
    text: str
    flag: object


def preservation_payload(ratio):
    # Keep Decimal thresholds in checks; only response metadata uses JSON floats.
    # Positive thresholds below float range must not become the forbidden zero.
    return {"length_ratio": {key: max(float(value), math.nextafter(0.0, 1.0))
                             for key, value in ratio.items()}}


def valid(condition):
    if not condition:
        raise ValidationError("invalid_response", None)


def nonblank(value):
    return (type(value) is str and not re.search(r"[\ud800-\udfff]", value)
            and re.search("[^" + SPACE + "]", value) is not None)


def unique_object(pairs):
    if len(dict(pairs)) != len(pairs):
        raise ValueError()
    return dict(pairs)


def reject_constant(_):
    raise ValueError()


def parse_generation(result, expected_items, diagnoses=None):
    if result.finish != "stop":
        code = ("generation_truncated" if result.finish == "truncated" else
                "provider_error" if result.finish == "blocked" else "invalid_response")
        raise ValidationError(code, None)
    data = None
    if type(result.raw_json) is str:
        try:
            data = json.loads(result.raw_json, object_pairs_hook=unique_object, parse_constant=reject_constant)
        except (ValueError, RecursionError):
            pass
    # Raising outside the handler avoids retaining JSONDecodeError.doc as context.
    valid(type(data) is dict and set(data) == {"items"})
    valid(type(data["items"]) is list and bool(data["items"]))
    expected = [item.id for item in expected_items]
    candidates = {}
    for item in data["items"]:
        valid(type(item) is dict and set(item) == {"id", "text", "flag"})
        identity = item["id"]
        valid(type(identity) is str and re.fullmatch(ID, identity) is not None)
        valid(identity not in candidates)
        valid(nonblank(item["text"]))
        flag = item["flag"]
        if flag is not None:
            valid(type(flag) is dict and set(flag) == {"kind", "reason"})
            valid(flag["kind"] == "unfixable" and nonblank(flag["reason"]))
            valid(len(flag["reason"]) <= 160)
            flag = MappingProxyType(flag)
        candidates[identity] = Candidate(identity, item["text"], flag)
    valid(len(expected) == len(set(expected)) and set(candidates) == set(expected))
    if diagnoses is not None:
        from .diagnosis import check_no_issue
        check_no_issue(diagnoses, expected_items, tuple(candidates.values()))
    if any(len(item.text) > 16000 for item in candidates.values()) or sum(len(item.text) for item in candidates.values()) > 16000:
        raise ValidationError("output_limit", None)
    return tuple(candidates[identity] for identity in expected)


def output_schema(tool):
    from .requests import LANGUAGE, MESSAGES

    valid(tool in ("polish_text", "lint_text"))
    def obj(**properties):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    def array(items, **limits):
        return {"type": "array", "items": items, **limits}
    def nullable(schema):
        return {"anyOf": [schema, {"type": "null"}]}
    def pattern(expression):
        return {"type": "string", "pattern": "^(?:" + expression + r")(?![\s\S])"}
    string = pattern(r"[^\ud800-\udfff]*")
    body = {**string, "minLength": 1, "allOf": [{"pattern": "[^" + SPACE + "]"}]}
    integer = {"type": "integer", "minimum": 0}
    boolean = {"type": "boolean"}
    usage = obj(**{key: nullable(integer) for key in ("input_tokens", "output_tokens", "total_tokens")})
    cost = nullable(obj(amount=pattern(r"[0-9]+\.[0-9]{6}"), currency=pattern(r"[A-Z]{3}")))
    language = {**pattern(LANGUAGE), "maxLength": 35}
    metadata = dict(schema_version={"type": "integer", "const": 1}, language=language,
                    rules_version=pattern(r"sha256:[0-9a-f]{64}"), common_version=pattern(r"sha256:[0-9a-f]{64}"),
                    model={"type": "null"} if tool == "lint_text" else {**string, "minLength": 1},
                    usage=usage, cost=cost, latency_ms=integer, model_calls={**integer, "maximum": 2})
    finding = obj(rule_id=pattern(LANGUAGE + r"-(vocabulary|syntax|structure|translation|context)-[0-9]{3}"), start=integer, end={**integer, "minimum": 1},
                  matched={**string, "maxLength": 160}, matched_truncated=boolean, message={**body, "maxLength": 160})
    findings = dict(findings=array(finding, maxItems=100), findings_truncated=boolean)
    checks = array({"enum": ["protected_terms", "numbers", "urls", "variables", "length_ratio"]},
                   minItems=1, uniqueItems=True)
    flag = nullable({"oneOf": [obj(kind={"const": "unfixable"}, reason={**body, "maxLength": 160},
                                   checks=array(string, maxItems=0)),
                              obj(kind={"const": "rejected"}, reason={"const": "Preservation checks failed."}, checks=checks)]})
    item = dict(text={**body, "maxLength": 16000}, flag=flag, regenerated=boolean,
                protected_terms=array({**body, "maxLength": 128}, uniqueItems=True, maxItems=2048), **findings)
    success = dict(**metadata, status={"const": "ok"}, protected_terms_checked=integer,
                   preservation=obj(length_ratio=obj(min={"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                                                      max={"type": "number", "minimum": 1, "maximum": 4})))
    if tool == "polish_text":
        success["model"] = {**string, "minLength": 1}
        variants = [obj(**success, **item), obj(**success, items=array(obj(id=pattern(ID), **item), minItems=1, maxItems=32))]
    else:
        success.update(model={"type": "null"}, usage=obj(**{key: {"type": "integer", "const": 0} for key in usage["properties"]}),
                       cost={"type": "null"}, model_calls={"type": "integer", "const": 0},
                       preservation={"type": "null"}, protected_terms_checked={"type": "integer", "const": 0})
        variants = [obj(**success, **findings)]
    safe_field = nullable(pattern(r"(?:text|items(?:\[(?:0|[1-9][0-9]*)\](?:\.(?:id|text|context))?)?|format|audience|purpose|tone|message|language|context|degree)"))
    errors = []
    for code, message in MESSAGES.items():
        field = safe_field if code in ("invalid_input", "unsupported_language", "input_limit") else {"type": "null"}
        errors.append(obj(code={"const": code}, message={"const": message}, field=field))
    variants.append(obj(**{**metadata, "language": nullable(language)}, status={"const": "error"},
                        error={"oneOf": errors}, model_called=boolean, regeneration_attempted=boolean))
    # MCP discovery requires outputSchema.type; every variant is an object, so the top-level type is exact.
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "oneOf": variants}


def validate_final(payload, *, schema=None, rewrite=False, check_limits=True):
    import math
    from jsonschema import Draft202012Validator, validators

    def scalar_tree(value):
        if type(value) is dict:
            return all(type(k) is str and scalar_tree(k) and scalar_tree(v) for k, v in value.items())
        if type(value) is list:
            return all(scalar_tree(v) for v in value)
        if type(value) is str:
            return re.search(r"[\ud800-\udfff]", value) is None
        return value is None or type(value) in (bool, int) or type(value) is float and math.isfinite(value)
    intact = False
    try:
        intact = scalar_tree(payload)
    except RecursionError:
        pass
    valid(intact and type(payload) is dict)
    tool = "lint_text" if payload.get("model") is None else "polish_text"
    schema = output_schema(tool) if schema is None else schema
    # Body limits must not hide another item's invalid shape or blank text.
    for variant in schema["oneOf"]:
        properties = variant["properties"]
        if "text" in properties:
            properties["text"].pop("maxLength", None)
        if "items" in properties:
            properties["items"]["items"]["properties"]["text"].pop("maxLength", None)
    checker = Draft202012Validator.TYPE_CHECKER.redefine("integer", lambda _, value: type(value) is int)
    valid(validators.extend(Draft202012Validator, type_checker=checker)(schema).is_valid(payload))
    calls, usage, cost = payload["model_calls"], payload["usage"], payload["cost"]
    if tool == "lint_text" or calls == 0:
        valid(calls == 0 and all(value == 0 for value in usage.values()) and cost is None)
    if usage["input_tokens"] is None or usage["output_tokens"] is None:
        valid(cost is None)
    if payload["status"] == "error":
        valid(payload["model_called"] == (calls > 0))
        valid(not payload["regeneration_attempted"] or calls >= 3) if rewrite else valid(payload["regeneration_attempted"] == (calls == 2))
        if payload["error"]["code"] in ("invalid_input", "unsupported_language", "input_limit"):
            valid(calls == 0 and not payload["regeneration_attempted"])
        items = []
    else:
        items = payload.get("items", [payload] if "text" in payload else [])
        if items:
            valid(calls > 0)
            valid(len({item["id"] for item in items}) == len(items) if "items" in payload else True)
            retried = any(item["regenerated"] for item in items)
            valid(not retried or calls >= 3) if rewrite else valid(retried == (calls == 2))
            valid(payload["protected_terms_checked"] == len({term for item in items for term in item["protected_terms"]}))
        for item in items:
            valid(item["protected_terms"] == sorted(item["protected_terms"]))
            if item["flag"] and item["flag"]["kind"] == "rejected":
                valid(item["regenerated"])
                order = ["protected_terms", "numbers", "urls", "variables", "length_ratio"]
                valid(item["flag"]["checks"] == sorted(item["flag"]["checks"], key=order.index))
        for item in items or [payload]:
            keys = [(f["start"], f["end"], f["rule_id"]) for f in item["findings"]]
            valid(keys == sorted(set(keys)))
            valid(not item["findings_truncated"] or len(keys) == 100)
            for finding in item["findings"]:
                valid(re.fullmatch(re.escape(payload["language"]) + r"-(vocabulary|syntax|structure|translation|context)-[0-9]{3}", finding["rule_id"]) is not None)
                start, end = finding["start"], finding["end"]
                valid(start < end and finding["matched_truncated"] == (end - start > 160))
                valid(len(finding["matched"]) == min(end - start, 160))
                if "text" in item:
                    valid(end <= len(item["text"]) and finding["matched"] == item["text"][start:min(end, start + 160)])
    if not check_limits:
        return
    if sum(len(item["text"]) for item in items) > 16000:
        raise ValidationError("output_limit", None)
    encoded = None
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, OverflowError, RecursionError):
        pass
    valid(encoded is not None)
    if len(encoded) > 1048576:
        raise ValidationError("output_limit", None)


def tool_output_schema(tool):
    schema = output_schema(tool)
    if tool == "polish_text":
        from .rewrite_response import rewrite_output_schema
        schema["oneOf"].extend(rewrite_output_schema()["oneOf"])
    return schema
