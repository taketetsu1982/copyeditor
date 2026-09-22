import json
from pathlib import Path

import pytest

from copyeditor.config import load_config
from copyeditor.providers.base import GenerationResult, ProviderFailure, Usage
from copyeditor.edit_protocol import validate_final
from copyeditor.providers.base import SourceItem
from copyeditor.rules import load_rules
from copyeditor.service import Service


@pytest.fixture
def setup():
    config = load_config(Path("absent-config"), {"GOOGLE_CLOUD_PROJECT": "test"})
    return config, load_rules(Path("rules"), None)


class Fake:
    def __init__(self, queue):
        self.queue, self.inputs, self.created = list(queue), [], 0

    def factory(self):
        self.created += 1
        return self

    async def estimate_input(self, value): return 0

    async def generate(self, value):
        self.inputs.append(value)
        assert self.queue, "Unexpected third call"
        result = self.queue.pop(0)
        if isinstance(result, Exception): raise result
        return result


def batch(*texts, finish="stop", ids=None, flag=None):
    ids = ids or (["text"] if len(texts) == 1 else ["a", "b"])
    return GenerationResult(json.dumps({"items": [dict(id=id, text=text, flag=flag, diagnosis=None) for id, text in zip(ids, texts)]}), finish, Usage(1, 2, 3))


async def run(setup, queue, arguments, tool="polish"):
    arguments = {"language": "en", **arguments}
    fake = Fake(queue)
    result = await getattr(Service(*setup, fake.factory), tool)(arguments)
    originals = tuple(SourceItem(i["id"], i["text"], i.get("context", "")) for i in arguments.get("items", [dict(id="text", text=arguments.get("text", ""))]))
    validate_final(result, originals, tool="polish_text" if tool == "polish" else "lint_text")
    assert not fake.queue
    return result, fake




@pytest.mark.asyncio
async def test_ac_02_2_ac_02_10_ctr01_retry_original_subset_and_order(setup):
    args = dict(items=[dict(id="a", text="Pay 10.", context="note"), dict(id="b", text="Hello.")], tone="friendly")
    result, fake = await run(setup, [batch("Pay 11.", "Hello."), batch("Pay 12.", ids=["a"])], args)
    first, retry = fake.inputs
    assert retry.items == (first.items[0],) and retry.background == first.background
    assert retry.system_instruction == first.system_instruction
    assert [i["id"] for i in result["items"]] == ["a", "b"]
    assert result["items"][0]["text"] == "Pay 12." and result["items"][0]["flag"]["checks"] == ["numbers"]
    assert [i["regenerated"] for i in result["items"]] == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("second,code", [
    (GenerationResult('{"items":[]}', "stop", Usage(None, None, None)), "invalid_response"),
    (batch(" "), "invalid_response"), (batch("x", finish="truncated"), "generation_truncated"),
    (ProviderFailure("provider_timeout", Usage(None, None, None)), "provider_timeout")])
async def test_ac_02_3_ac_02_4_ctr01_invalid_retry_discards_batch(setup, second, code):
    result, fake = await run(setup, [batch("Pay 11."), second], {"text": "Pay 10."})
    assert result["error"]["code"] == code and "text" not in result and "items" not in result
    assert result["regeneration_attempted"] and len(fake.inputs) == 2


@pytest.mark.asyncio
async def test_ac_02_2_ctr01_merged_output_limit(setup):
    args = {"items": [dict(id="a", text="a"*4000), dict(id="b", text="1" + "a"*4999)]}
    result, fake = await run(setup, [batch("a"*8000, "2" + "a"*7999), batch("1" + "a"*8000, ids=["b"])], args)
    assert result["error"]["code"] == "output_limit" and len(fake.inputs) == 2


@pytest.mark.asyncio
async def test_ac_02_2_ac_02_11_ctr02_unfixable_retains_original_before_checks(setup):
    args = {"text": "Pay 10."}
    result, fake = await run(setup, [batch("Pay 10.", flag=dict(kind="unfixable", reason="Cannot edit."))], args)
    assert result["text"] == args["text"] and result["flag"]["checks"] == []
    assert not result["regenerated"] and len(fake.inputs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("args,tool,code", [({}, "polish", "invalid_input"),
    ({"text": "<p", "format": "html"}, "polish", "invalid_input"),
    ({"text": "Hi", "language": "xx"}, "polish", "unsupported_language"),
    ({"text": "Hello.", "language": "en"}, "lint", None)])
async def test_ctr01_validation_and_lint_never_construct_provider(setup, args, tool, code):
    result, fake = await run(setup, [], args, tool)
    assert fake.created == 0 and not fake.inputs and result.get("providers", [result])[0]["model_calls"] == 0
    if code: assert result["error"]["code"] == code
    else: assert result["model"] is None and result["cost"] is None


@pytest.mark.asyncio
async def test_ac_02_3_ctr01_unexpected_failure_is_fixed_and_metered(setup, capsys):
    result, fake = await run(setup, [RuntimeError("PRIVATE")], {"text": "Hello.", "language": "en"})
    assert result["error"]["code"] == "provider_error" and result.get("providers", [result])[0]["model_calls"] == 1
    assert all(v is None for v in result["providers"][0]["usage"].values())
    assert "PRIVATE" not in json.dumps(result) and capsys.readouterr() == ("", "")


@pytest.mark.asyncio
async def test_ac_02_11_ctr02_terms_and_final_lint_use_returned_candidate(setup):
    config, snapshot = setup
    rules = snapshot.languages["en"]._replace(protected_terms=("Hello",))
    snapshot = snapshot._replace(languages={**snapshot.languages, "en": rules})
    result, fake = await run((config, snapshot), [batch("Other."), batch("Other.")], {"text": "Hello.", "language": "en"})
    assert result["protected_terms"] == ["Hello"] and result["protected_terms_checked"] == 1
    assert "Hello" in fake.inputs[0].system_instruction and result["text"] == "Other."
    from copyeditor.lint import lint_response
    assert result["findings"] == lint_response("Other.", rules)["findings"]


@pytest.mark.asyncio
async def test_ctr01_factory_and_lint_exceptions_never_leak(setup, monkeypatch):
    def fail(*args, **kwargs): raise RuntimeError("PRIVATE")
    service = Service(*setup, fail)
    result = await service.polish({"text": "Hello.", "language": "en"})
    assert result["error"]["code"] == "internal_error" and result.get("providers", [result])[0]["model_calls"] == 0
    monkeypatch.setattr("copyeditor.edit_service.lint_response", fail)
    result = await service.lint({"text": "Hello.", "language": "en"})
    assert result["error"]["code"] == "internal_error" and result["model"] is None
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.asyncio
async def test_ac_02_3_ctr01_parallel_requests_keep_metrics_local(setup):
    import asyncio
    providers = []
    def factory():
        fake = Fake([batch("Hello.")]); providers.append(fake)
        return fake
    service = Service(*setup, factory)
    results = await asyncio.gather(service.polish({"text": "Hello.", "language": "en"}), service.polish({"text": "Hello.", "language": "en"}))
    assert len(providers) == 2
    for result in results:
        assert result.get("providers", [result])[0]["model_calls"] == 1 and result["providers"][0]["usage"]["total_tokens"] == 3
        validate_final(result, (SourceItem("text", "Hello.", ""),))


@pytest.mark.asyncio
async def test_ctr01_final_validator_failure_returns_fixed_error(setup, monkeypatch):
    def fail(*args, **kwargs): raise RuntimeError("PRIVATE")
    monkeypatch.setattr("copyeditor.edit_pipeline.validate_final", fail)
    result, fake = await run(setup, [batch("Hello.")], {"text": "Hello.", "language": "en"})
    assert result["error"]["code"] == "internal_error" and result.get("providers", [result])[0]["model_calls"] == 1
    assert "PRIVATE" not in json.dumps(result)
