import re
from typing import NamedTuple

from .providers.base import Background, SourceItem

SPACE = "\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"
ID = r"[A-Za-z0-9_-]{1,64}"
LANGUAGE = r"[a-z]{2,3}(-[a-z0-9]{2,8})*"
MESSAGES = {"invalid_input": "Invalid tool arguments.",
            "unsupported_language": "Language is not installed; the model was not called.",
            "input_limit": "Input limits exceeded; the model was not called."}


class ValidationError(ValueError):
    def __init__(self, code="invalid_input", field=None):
        self.code, self.field = code, field
        super().__init__(MESSAGES[code])


class Request(NamedTuple):
    items: tuple[SourceItem, ...]
    language: str
    format: str
    background: Background


def check(condition, field=None, code="invalid_input"):
    if not condition:
        raise ValidationError(code, field)


def string(value, field, body=False):
    check(type(value) is str, field)
    check(not re.search(r"[\ud800-\udfff]", value), field)
    check(not body or re.search("[^" + SPACE + "]", value) is not None, field)


def parse_request(tool, arguments, config, snapshot):
    check(tool in ("polish_text", "lint_text"))
    polish = tool == "polish_text"
    fields = ("text", "items", "format", *Background._fields, "language") if polish else ("text", "language")
    check(type(arguments) is dict)
    check(not set(arguments) - set(fields))
    check(("text" in arguments) != ("items" in arguments) if polish else "text" in arguments)
    items = []
    if "text" in arguments:
        string(arguments["text"], "text", True)
        items.append(SourceItem("text", arguments["text"], ""))
    else:
        check(type(arguments["items"]) is list, "items")
        seen = set()
        for i, item in enumerate(arguments["items"]):
            check(type(item) is dict and not set(item) - {"id", "text", "context"}, f"items[{i}]")
        for name in ("id", "text", "context"):
            for i, item in enumerate(arguments["items"]):
                path = f"items[{i}].{name}"
                value = item.get(name, "" if name == "context" else None)
                string(value, path, name == "text")
                if name == "id":
                    check(re.fullmatch(ID, value) is not None and value not in seen, path)
                    seen.add(value)
        items = [SourceItem(item["id"], item["text"], item.get("context", "")) for item in arguments["items"]]
    format = arguments.get("format", "text")
    check(type(format) is str and format in ("text", "markdown", "html"), "format")
    check(format != "html" or "text" in arguments, "format")
    for field in Background._fields:
        if polish:
            string(arguments.get(field, ""), field)
    language = arguments.get("language", config["default_language"])
    string(language, "language")
    check(len(language) <= 35 and re.fullmatch(LANGUAGE, language) is not None, "language")
    check(language in snapshot.languages, "language", "unsupported_language")
    background = Background(*(arguments.get(field, "") for field in Background._fields))
    check(1 <= len(items) <= 32, "items", "input_limit")
    for name, limit in (("text", 12000), ("context", 1000)):
        for i, item in enumerate(items):
            path = f"items[{i}]." if "items" in arguments else ""
            check(len(getattr(item, name)) <= limit, path + name, "input_limit")
        check(sum(len(getattr(item, name)) for item in items) <= (12000 if name == "text" else 4000), "items", "input_limit")
    for field, value in zip(Background._fields, background):
        check(len(value) <= 1000, field, "input_limit")
    bodies, contexts, backgrounds = sum(len(i.text) for i in items), sum(len(i.context) for i in items), sum(map(len, background))
    check(backgrounds <= 4000 and contexts + backgrounds <= 4000 and bodies + contexts + backgrounds <= 16000,
          None, "input_limit")
    return Request(tuple(items), language, format, background)


def input_schema(tool, config, snapshot):
    check(tool in ("polish_text", "lint_text"))
    def text(limit, body=False):
        result = {"type": "string", "maxLength": limit, "pattern": r"^[^\ud800-\udfff]*$"}
        if body:
            result.update(minLength=1, allOf=[{"pattern": "[^" + SPACE + "]"}])
        else:
            result["default"] = ""
        return result
    properties = {"text": text(12000, True), "language": {"type": "string", "enum": sorted(snapshot.languages),
                  "default": config["default_language"], "maxLength": 35, "pattern": "^" + LANGUAGE + "$"}}
    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if tool == "lint_text":
        schema["required"] = ["text"]
    else:
        properties.update(items={"type": "array", "minItems": 1, "maxItems": 32, "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "text"],
            "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^" + ID + r"(?![\s\S])"},
                           "text": text(12000, True), "context": text(1000)}}},
            format={"type": "string", "enum": ["text", "markdown", "html"], "default": "text"})
        properties.update({field: text(1000) for field in Background._fields})
        schema["oneOf"] = [{"required": ["text"], "not": {"required": ["items"]}},
                           {"required": ["items"], "not": {"required": ["text"]},
                            "properties": {"format": {"enum": ["text", "markdown"]}}}]
        schema["description"] = ("Decoded Unicode code points, without normalization: body total <=12000; contexts total <=4000; "
            "background total <=4000; contexts plus background <=4000; body plus contexts plus background <=16000. "
            "Item IDs must be unique. Aggregate budgets and ID uniqueness are validated by the server.")
    return schema
