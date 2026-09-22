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
    from copyeditor.edit_protocol import output_schema
    assert case["generation"] == 4
    return output_schema(case["tool"])


def validate_fixture(payload, case):
    from copyeditor.edit_protocol import validate_final
    validate_final(payload, source_items(case), tool=case["tool"],
                   expected_enabled=case["judgment_enabled"], registry=fixture_registry,
                   format=case["input"].get("format", "text"))


def generation_result(response):
    from copyeditor.providers.base import GenerationResult, ProviderFailure, Usage
    if isinstance(response, (GenerationResult, ProviderFailure)):
        return response
    return GenerationResult(json.dumps({key: value for key, value in response.items() if key != "finish"}),
                            response.get("finish", "stop"), Usage(None, None, None))


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
    """Exercise the complete prepared entry before the atomic public cutover."""
    import httpx
    from copyeditor.config_v4 import load_config
    from copyeditor.edit_service import EditService
    from copyeditor.providers.base import Usage
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from copyeditor.judgment_v2 import POLICY_ID
    from copyeditor.providers.typesafe import TypeSafe
    from copyeditor.rules import load_rules
    assert case["generation"] == 4
    editing = case["tool"] == "polish_text"
    enabled, arguments = case["judgment_enabled"], case["input"]
    env = {"GOOGLE_CLOUD_PROJECT": "fixture", "COPYEDITOR_MODEL": "test-model"}
    if enabled:
        env.update(COPYEDITOR_JUDGMENT_ENABLED="true", COPYEDITOR_JUDGMENT_THRESHOLDS_VERSION="synthetic",
                   TYPESAFE_API_KEY="synthetic")
    config = load_config(root / "absent-config", env, thresholds=FIXTURE_THRESHOLDS, pairs={(POLICY_ID, "synthetic")})
    queue, inputs, estimates, wires, generated = ProviderQueue(case["provider"]), [], [], [], []
    class Provider:
        async def generate(self, value):
            inputs.append(value)
            response = generation_result(queue.take())
            generated.append(response)
            return response
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
    try:
        with TemporaryDirectory() as directory:
            base = Path(directory)
            for source in (root / "rules").glob("*.md"):
                raw = source.read_text()
                if source.stem not in ("common", "README") and "Default style: " not in raw:
                    raw = raw.replace("## Context weights\n", "## Context weights\nDefault style: Natural expression.\n")
                (base / source.name).write_text(raw)
            service = EditService(config, load_rules(base, None), Provider, adapter, registry=fixture_registry)
            payload = await getattr(service, "polish" if editing else "lint")(arguments)
    finally:
        if adapter is not None:
            await adapter.aclose()
    queue.assert_exhausted()
    assert len(inputs) == len(case["provider"]), "Provider underflow must not be hidden by service errors"
    assert estimates == inputs
    assert (payload["providers"][0]["model_calls"] if editing else payload["model_calls"]) == len(inputs)
    assert (payload["providers"][1]["model_calls"] if enabled and editing else 0) == len(wires)
    observations = [[response.usage for response in generated]]
    if enabled and editing:
        observations.append([Usage(None, None, None)] * len(wires))
    for row, samples in zip(payload["providers"] if editing else [payload], observations, strict=True):
        for key in Usage._fields:
            values = [getattr(sample, key) for sample in samples]
            assert row["usage"][key] == (None if None in values else sum(values)), "Provider usage differs from observations"
        assert row["cost"] is None
    assert payload["cost"] is None
    assert_fixture(payload, case)
    return payload, inputs


def generation4_cases(path):
    """Translate historical fixture material with explicit current-contract expectations."""
    from copy import deepcopy
    cases = deepcopy(load_cases(path, "contract-case"))
    for case in cases:
        name, arguments, responses, expected = case["name"], case["input"], case["provider"], case["expect"]
        case.update(generation=4, judgment_enabled=False)
        arguments.setdefault("language", "en")
        expected["schema_version"] = 4
        if case["tool"] == "lint_text":
            continue
        rewrite = arguments.get("degree") == "rewrite"
        if rewrite:
            diagnoses = responses[0]["diagnoses"]
            descriptions = {item["id"]: item["reason"] or "No expression change needed." for item in diagnoses}
            if len(responses) == 1:
                responses = [dict(items=[dict(id=item["id"], text=arguments["text"], flag=None,
                    diagnosis=descriptions[item["id"]]) for item in diagnoses])]
            else:
                responses = responses[1:]
            for response in responses:
                for item in response.get("items", []):
                    item["diagnosis"] = descriptions.get(item["id"], "No expression change needed.")
            if name == "rewrite_missing_diagnosis":
                responses = [dict(items=[dict(id="text", text=arguments["text"], flag=None)])]
            if "diagnosis" in expected:
                expected["diagnosis"] = descriptions["text"]
            for item in expected.get("items", []):
                if "diagnosis" in item: item["diagnosis"] = descriptions[item["id"]]
            if name == "rewrite_no_issue_changed":
                expected = dict(status="ok", schema_version=4, text="Hi.", flag=None, regenerated=False)
        else:
            for response in responses:
                for item in response.get("items", []): item["diagnosis"] = None
        if name == "partial_rejection":
            expected["items"][0]["text"] = "Pay 12."
        if name == "shared_html_retry":
            expected = dict(status="ok", schema_version=4, text="<div>Pay 10.</div>", flag=None, regenerated=True)
        if name == "html_incomplete_candidate":
            responses = responses[:1]
            expected = dict(status="ok", schema_version=4, text='<p title="x', flag=None, regenerated=False)
        expected.pop("model_calls", None)
        expected["providers"] = [dict(role="editing", model_calls=len(responses), estimation_calls=len(responses))]
        case.update(provider=responses, expect=expected)
    return cases
