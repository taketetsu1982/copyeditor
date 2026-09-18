import asyncio
import json

import httpx
import pytest
from google.auth.credentials import AnonymousCredentials

from copyeditor.diagnosis import Diagnosis, DiagnosticItem
from copyeditor.prompt import system_instruction
from copyeditor.providers import vertex
from copyeditor.providers.base import Background, GenerationInput, ProviderFailure, SourceItem, Usage


def generation(stage):
    diagnoses = (DiagnosticItem("text", Diagnosis("issue", "body", "DIAGNOSIS_SENTINEL")),) if stage == "rewrite" else ()
    return GenerationInput((SourceItem("text", "body INPUT_SENTINEL", "CONTEXT_SENTINEL"),), "ja", "text",
        Background("", "", "", "BACKGROUND_SENTINEL"),
        system_instruction("COMMON", "LANGUAGE", (), stage), stage, diagnoses)


@pytest.fixture
def sdk(monkeypatch):
    calls, behavior = [], {"count": {"totalTokens": 7}, "status": 200, "delay": 0}
    async def handle(request):
        calls.append((request.url.path, json.loads(request.content)))
        await asyncio.sleep(behavior["delay"])
        if "error" in behavior:
            raise behavior["error"]
        if request.url.path.endswith(":countTokens"):
            body = behavior["count"]
        else:
            body = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}}],
                    "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20,
                                      "thoughtsTokenCount": 3, "totalTokenCount": 33}}
        return httpx.Response(behavior["status"], json=body)
    credentials = AnonymousCredentials()
    credentials.token = "synthetic-transport-fixture"
    monkeypatch.setattr(vertex.google.auth, "default", lambda **_: (credentials, "ignored"))
    monkeypatch.setattr(vertex.httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(handle))
    config = {"vertex.project": "fixture", "vertex.location": "global", "model": "gemini-3.1-flash-lite", "thinking": "low"}
    return vertex.Vertex(config), calls, behavior


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["polish", "diagnose", "rewrite"])
async def test_ac_07_6_ac_07_7_ctr01_real_sdk_count_and_generate_share_complete_prompt(sdk, stage, capsys):
    provider, calls, _ = sdk
    try:
        data = generation(stage)
        assert await provider.estimate_input(data) == 7
        result = await provider.generate(data)
        assert result.finish == "stop" and result.usage == Usage(10, 23, 33)
        assert len(calls) == 2
        counted, generated = calls[0][1], calls[1][1]
        assert calls[0][0].endswith(":countTokens") and calls[1][0].endswith(":generateContent")
        assert counted["contents"] == generated["contents"]
        assert counted["systemInstruction"] == generated["systemInstruction"]
        assert counted["generationConfig"] == generated["generationConfig"]
        config = generated["generationConfig"]
        assert config["maxOutputTokens"] == 8192 and config["temperature"] == 0
        assert config["thinkingConfig"]["thinking_level"] == "LOW"
        schema = config["responseJsonSchema"]
        assert schema["required"] == (["diagnoses"] if stage == "diagnose" else ["items"])
        payload = json.loads(generated["contents"][0]["parts"][0]["text"])
        assert ("diagnoses" in payload) == (stage == "rewrite")
        instruction = json.dumps(generated["systemInstruction"])
        assert "SENTINEL" not in instruction
        if stage == "rewrite":
            assert payload["diagnoses"][0]["reason"] == "DIAGNOSIS_SENTINEL"
        assert capsys.readouterr() == ("", "")
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("estimate", [None, True, -1, 1.5, "7"])
async def test_ctr01_invalid_rpc_estimates_are_not_coerced_or_retried(sdk, estimate):
    provider, calls, behavior = sdk
    behavior["count"] = {"totalTokens": estimate}
    try:
        assert await provider.estimate_input(generation("diagnose")) == ProviderFailure("provider_error", Usage(None, None, None))
        assert len(calls) == 1
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["estimate_input", "generate"])
@pytest.mark.parametrize("failure,code", [(httpx.ReadTimeout("PRIVATE"), "provider_timeout"),
                                          (RuntimeError("PRIVATE"), "provider_error"), (503, "provider_error")])
async def test_ac_07_11_ctr01_sdk_failures_are_fixed_and_never_retried(sdk, operation, failure, code, capsys):
    provider, calls, behavior = sdk
    behavior["status" if type(failure) is int else "error"] = failure
    try:
        result = await getattr(provider, operation)(generation("diagnose"))
        assert result == ProviderFailure(code, Usage(None, None, None))
        assert len(calls) == 1 and capsys.readouterr() == ("", "")
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["estimate_input", "generate"])
async def test_ctr01_remaining_request_deadline_cancels_real_sdk_await(sdk, operation):
    provider, calls, behavior = sdk
    behavior["delay"] = 10
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await getattr(provider, operation)(generation("diagnose"))
        assert len(calls) == 1
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_inv9_stage_and_frozen_subset_validation_precede_rpc(sdk):
    provider, calls, _ = sdk
    try:
        for data in (generation("diagnose")._replace(stage="other"), generation("rewrite")._replace(diagnoses=()),
                     generation("polish")._replace(diagnoses=generation("rewrite").diagnoses)):
            assert (await provider.estimate_input(data)).code == "provider_error"
            assert (await provider.generate(data)).code == "provider_error"
        assert calls == []
    finally:
        await provider.aclose()
