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
