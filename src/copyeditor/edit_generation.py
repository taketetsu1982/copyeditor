"""Generation-4 candidate/diagnosis integrity, before any preservation checks."""
import json
import re
from types import MappingProxyType
from typing import NamedTuple

from .requests import ID, ValidationError
from .responses import nonblank, reject_constant, unique_object, valid


class EditCandidate(NamedTuple):
    id: str
    text: str
    flag: object
    diagnosis: str | None


def generation_schema(stage):
    valid(stage in ("polish", "rewrite"))
    def obj(**fields):
        return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}
    string = {"type": "string"}
    flag = {"anyOf": [{"type": "null"}, obj(kind={"const": "unfixable"}, reason=string)]}
    return obj(items={"type": "array", "items": obj(id=string, text=string, flag=flag,
        diagnosis={"type": "null"} if stage == "polish" else string)})


def parse_generation(result, expected_items, *, stage):
    if result.finish != "stop":
        code = ("generation_truncated" if result.finish == "truncated" else
                "provider_error" if result.finish == "blocked" else "invalid_response")
        raise ValidationError(code, None)
    valid(stage in ("polish", "rewrite"))
    data = None
    if type(result.raw_json) is str:
        try:
            data = json.loads(result.raw_json, object_pairs_hook=unique_object, parse_constant=reject_constant)
        except (ValueError, RecursionError):
            pass
    # Decoder exceptions retain source text, so raise only after leaving their handler.
    valid(type(data) is dict and set(data) == {"items"})
    valid(type(data["items"]) is list and bool(data["items"]))
    originals = {item.id: item.text for item in expected_items}
    valid(len(originals) == len(expected_items))
    candidates = {}
    for item in data["items"]:
        valid(type(item) is dict and set(item) == {"id", "text", "flag", "diagnosis"})
        identity, text, flag, diagnosis = (item[key] for key in ("id", "text", "flag", "diagnosis"))
        valid(type(identity) is str and re.fullmatch(ID, identity) is not None)
        valid(identity in originals and identity not in candidates and nonblank(text))
        if stage == "polish":
            valid(diagnosis is None)
        else:
            valid(nonblank(diagnosis) and "\r" not in diagnosis and "\n" not in diagnosis)
        if flag is not None:
            valid(type(flag) is dict and set(flag) == {"kind", "reason"})
            valid(flag["kind"] == "unfixable" and nonblank(flag["reason"]) and len(flag["reason"]) <= 160)
            valid(text == originals[identity])
            flag = MappingProxyType(flag)
        candidates[identity] = EditCandidate(identity, text, flag, diagnosis)
    valid(set(candidates) == set(originals))
    ordered = tuple(candidates[item.id] for item in expected_items)
    # Integrity of every item takes precedence over all body and diagnosis length caps.
    if (sum(len(item.text) for item in ordered) > 16000
            or any(len(item.diagnosis or "") > 320 for item in ordered)
            or sum(len(item.diagnosis or "") for item in ordered) > 8192):
        raise ValidationError("output_limit", None)
    return ordered
