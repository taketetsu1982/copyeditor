import json
import re
from collections import deque

MAX_STRING = 1_048_576


def expand(value):
    if isinstance(value, dict):
        if "$repeat" in value or "$concat" in value:
            if len(value) != 1:
                raise ValueError("Expansion must have exactly one key")
            if "$repeat" in value:
                args = value["$repeat"]
                if not (isinstance(args, list) and len(args) == 2
                        and isinstance(args[0], str) and type(args[1]) is int
                        and args[1] >= 0 and len(args[0]) * args[1] <= MAX_STRING):
                    raise ValueError("Invalid repeat operands or size")
                return args[0] * args[1] if args[0] else ""
            if not isinstance(value["$concat"], list):
                raise ValueError("Concat requires an array")
            parts = []
            size = 0
            for part in value["$concat"]:
                part = expand(part)
                if not isinstance(part, str):
                    raise ValueError("Concat requires strings")
                size += len(part)
                if size > MAX_STRING:
                    raise ValueError("Expanded string exceeds limit")
                parts.append(part)
            return "".join(parts)
        return {key: expand(part) for key, part in value.items()}
    if isinstance(value, list):
        return [expand(part) for part in value]
    if isinstance(value, str) and len(value) > MAX_STRING:
        raise ValueError("Expanded string exceeds limit")
    return value


def load_cases(path, kind):
    text = path.read_text(encoding="utf-8")
    opening = rf"^```json {re.escape(kind)}\s*\n"
    blocks = re.findall(opening + r"(.*?)^```[ \t]*$", text, re.M | re.S)
    if not blocks or len(blocks) != len(re.findall(opening, text, re.M)):
        raise ValueError("Missing or unclosed contract fixtures")
    return [expand(json.loads(block)) for block in blocks]


def assert_subset(actual, expected):
    assert type(actual) is type(expected), "Fixture types differ"
    if isinstance(expected, dict):
        assert expected.keys() <= actual.keys(), "Fixture keys are missing"
        for key in expected:
            assert_subset(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected), "Fixture array lengths differ"
        for left, right in zip(actual, expected):
            assert_subset(left, right)
    else:
        assert actual == expected, "Fixture values differ"


def assert_bytes_equal(actual, expected):
    assert type(actual) is bytes and type(expected) is bytes
    assert actual == expected, "Bundled bytes differ"


class ProviderQueue:
    def __init__(self, responses):
        self.responses = deque(responses)

    def take(self):
        assert self.responses, "Unexpected provider call"
        return self.responses.popleft()

    def assert_exhausted(self):
        assert not self.responses, "Unconsumed provider responses"


def source_items(case):
    from copyeditor.providers.base import SourceItem
    arguments = case.get("input", {})
    values = arguments.get("items", [dict(id="text", text=arguments.get("text", ""))])
    return tuple(SourceItem(value["id"], value["text"], value.get("context", "")) for value in values)


def is_rewrite(case):
    return case["tool"] == "polish_text" and case.get("input", {}).get("degree") == "rewrite"


def fixture_schema(case):
    from copyeditor.responses import output_schema
    from copyeditor.rewrite_response import rewrite_output_schema
    return rewrite_output_schema() if is_rewrite(case) else output_schema(case["tool"])


def validate_fixture(payload, case):
    from copyeditor.responses import validate_final
    from copyeditor.rewrite_response import validate_rewrite_final
    if is_rewrite(case):
        validate_rewrite_final(payload, source_items(case))
    else:
        validate_final(payload)


def generation_result(response):
    from copyeditor.providers.base import GenerationResult, ProviderFailure, Usage
    if isinstance(response, (GenerationResult, ProviderFailure)):
        return response
    return GenerationResult(json.dumps({key: value for key, value in response.items() if key != "finish"}),
                            response.get("finish", "stop"), Usage(None, None, None))


def rewrite_case():
    return dict(name="rewrite-fixture", tool="polish_text", input=dict(text="Hello.", language="en", degree="rewrite"),
        provider=[dict(diagnoses=[dict(id="text", status="no_issue", expression=None, reason=None)]),
                  dict(items=[dict(id="text", text="Hello.", flag=None)])],
        expect=dict(status="ok", schema_version=2, text="Hello.", model_calls=2, regenerated=False,
                    diagnosis=dict(status="no_issue", expression=None, reason=None)))
