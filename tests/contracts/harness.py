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
    if case.get("generation") == 4:
        from copyeditor.edit_protocol import output_schema
        return output_schema(case["tool"])
    from copyeditor.responses import output_schema
    from copyeditor.rewrite_response import rewrite_output_schema
    return rewrite_output_schema() if is_rewrite(case) else output_schema(case["tool"])


def validate_fixture(payload, case):
    if case.get("generation") == 4:
        from copyeditor.edit_protocol import validate_final
        validate_final(payload, source_items(case), tool=case["tool"],
                       expected_enabled=case["judgment_enabled"], registry=fixture_registry,
                       format=case["input"].get("format", "text"))
        return
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


# Synthetic thresholds are explicit test inputs, never production registration.
FIXTURE_THRESHOLDS = {"synthetic": dict(id="synthetic", floor=0.5, gap=0.2, meaning_floor=0.8)}


def fixture_registry(policy, threshold):
    from copyeditor.judgment_v2 import POLICY_ID, snapshot
    return snapshot(policy, threshold, thresholds=FIXTURE_THRESHOLDS, pairs={(POLICY_ID, "synthetic")})


def assert_fixture(payload, case):
    from jsonschema import Draft202012Validator
    Draft202012Validator(fixture_schema(case)).validate(payload)
    validate_fixture(payload, case)
    assert_subset(payload, case["expect"])


async def invoke_generation4(case, root):
    """Exercise the prepared pipeline; public input parsing switches in Task 164."""
    import httpx
    from copyeditor.config_v4 import load_config
    from copyeditor.edit_pipeline import polish
    from copyeditor.judged_metrics import EditMetrics
    from copyeditor.judgment_v2 import POLICY_ID
    from copyeditor.providers.base import Background
    from copyeditor.providers.typesafe import TypeSafe
    from copyeditor.requests import Request
    from copyeditor.rules import load_rules
    from copyeditor.service import Service
    assert case["generation"] == 4 and case["tool"] == "polish_text"
    enabled, arguments = case["judgment_enabled"], case["input"]
    env = {"GOOGLE_CLOUD_PROJECT": "fixture", "COPYEDITOR_MODEL": "test-model"}
    if enabled:
        env.update(COPYEDITOR_JUDGMENT_ENABLED="true", COPYEDITOR_JUDGMENT_THRESHOLDS_VERSION="synthetic",
                   TYPESAFE_API_KEY="synthetic")
    config = load_config(root / "absent-config", env, thresholds=FIXTURE_THRESHOLDS, pairs={(POLICY_ID, "synthetic")})
    queue, inputs, estimates, wires = ProviderQueue(case["provider"]), [], [], []
    class Provider:
        async def generate(self, value):
            inputs.append(value)
            return generation_result(queue.take())
        async def estimate_input(self, value):
            estimates.append(value)
            return 0
    def handler(wire):
        value = json.loads(wire.content)
        wires.append(value)
        checking = "originals" in value["state"]
        return httpx.Response(200, json=dict(model="jev-1.13.0", answers={key: dict(type="Noul",
            noul=0.9 if not checking or key.endswith("meaning") else 0.3) for key in value["questions"]}))
    adapter = TypeSafe(config.secrets["TYPESAFE_API_KEY"], transport=httpx.MockTransport(handler)) if enabled else None
    request = Request(source_items(case), arguments["language"], arguments.get("format", "text"),
                      Background(*(arguments.get(key, "") for key in Background._fields)), arguments["degree"])
    meter = EditMetrics(0, config["model"], config["pricing"], clock=lambda: 0,
                        degree=request.degree, judgment_enabled=enabled)
    service = Service(config, load_rules(root / "rules", None), Provider, adapter)
    try:
        payload = await polish(service, request, adapter, meter, items_route="items" in arguments, registry=fixture_registry)
    finally:
        if adapter is not None:
            await adapter.aclose()
    queue.assert_exhausted()
    assert len(inputs) == len(case["provider"]), "Provider underflow must not be hidden by service errors"
    assert estimates == inputs
    assert payload["providers"][0]["model_calls"] == len(inputs)
    assert (payload["providers"][1]["model_calls"] if enabled else 0) == len(wires)
    assert_fixture(payload, case)
    return payload, inputs
