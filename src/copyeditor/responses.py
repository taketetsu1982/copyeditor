import json
import re
from types import MappingProxyType
from typing import NamedTuple

from .requests import ID, SPACE, ValidationError


class Candidate(NamedTuple):
    id: str
    text: str
    flag: object


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


def parse_generation(result, expected_items):
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
    safe_field = nullable(pattern(r"(?:text|items(?:\[(?:0|[1-9][0-9]*)\](?:\.(?:id|text|context))?)?|format|audience|purpose|tone|message|language|context)"))
    errors = []
    for code, message in MESSAGES.items():
        field = safe_field if code in ("invalid_input", "unsupported_language", "input_limit") else {"type": "null"}
        errors.append(obj(code={"const": code}, message={"const": message}, field=field))
    variants.append(obj(**{**metadata, "language": nullable(language)}, status={"const": "error"},
                        error={"oneOf": errors}, model_called=boolean, regeneration_attempted=boolean))
    # MCP discovery requires outputSchema.type; every variant is an object, so the top-level type is exact.
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "oneOf": variants}
