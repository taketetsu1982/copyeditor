"""TypeSafe adapter observations with mock HTTP transport.
AC-08-7, AC-08-9: invalid/error responses fail without private details in logs.
AC-08-8: exact prepared payload; AC-08-10: unknown usage stays unknown.
AC-08-12: one attempt, bounded response/deadline and cancellation without retry."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from copyeditor.judgment import JudgmentBlock, JudgmentInput, JudgmentFailure
from copyeditor.judgment_v2_batch import prepare_judgments
from copyeditor.providers.base import Background, Usage
from copyeditor.providers.typesafe import TypeSafe


def prepared(phase="detect"):
    return prepare_judgments(JudgmentInput(phase, "ja", "text", Background("", "", "", ""), "",
        tuple(JudgmentBlock(i, "synthetic body", "", "candidate", None) for i in (2, 4))),
        candidate_round=0 if phase == "detect" else 1).requests[0]


def answer(request):
    answers = {key: dict(type="Noul", noul=0.9) for key in json.loads(request)["questions"]}
    return dict(model="jev-1.13.0", answers=answers, usage=dict(input_tokens=20, output_tokens=30))


def adapter(handler):
    return TypeSafe(SimpleNamespace(reveal=lambda: "synthetic-credential-sentinel"), transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,count", [("detect", 2), ("verify", 4)])
async def test_exact_prepared_request_and_complete_results(phase, count):
    request, seen = prepared(phase), []
    def handler(wire):
        seen.append(wire)
        assert wire.method == "POST" and str(wire.url) == "https://api.typesafe.ai/v1/systemone"
        assert wire.content == request
        assert wire.headers["Authorization"] == "Bearer synthetic-credential-sentinel"
        assert len(json.loads(wire.content)["questions"]) == count
        return httpx.Response(200, json=answer(request))
    client = adapter(handler)
    result = await client.evaluate(request)
    assert [block.ordinal for block in result.blocks] == [2, 4]
    assert result.usage == Usage(20, 30, None)
    assert all(block.choice is None and len(block.probabilities) == count // 2 for block in result.blocks)
    assert len(seen) == 1
    await client.aclose()
    assert (await client.evaluate(request)).code == "provider_error"
    assert len(seen) == 1 and client._client.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["model", "id", "extra_id", "type", "field", "missing", "boolean", "duplicate", "nan", "overflow", "oversize", "output", "top", "json"])
async def test_invalid_wire_fails_without_retry_or_private_details(kind, caplog, capsys):
    request, calls = prepared(), []
    data = answer(request)
    key = "b0002.gate"
    if kind == "model": data["model"] = "jev-latest"
    if kind == "id": del data["answers"][key]
    if kind == "extra_id": data["answers"]["foreign"] = data["answers"][key]
    if kind == "type": data["answers"][key]["type"] = "choice"
    if kind == "field": data["answers"][key]["reason"] = "PRIVATE"
    if kind == "missing": del data["answers"][key]["noul"]
    if kind == "boolean": data["answers"][key]["noul"] = True
    if kind == "output": data["usage"]["output_tokens"] = 65537
    if kind == "top": data["PRIVATE"] = "PRIVATE"
    body = json.dumps(data).encode()
    if kind == "duplicate": body = body.replace(b'"noul": 0.9', b'"noul": 0.9, "noul": 0.9', 1)
    if kind == "nan": body = body.replace(b'0.9', b'NaN', 1)
    if kind == "overflow": body = body.replace(b'20', b'1e999', 1)
    if kind == "oversize": body = b" " * 65537
    if kind == "json": body = b"PRIVATE invalid JSON"
    def handler(wire):
        calls.append(wire)
        return httpx.Response(200, content=body)
    client = adapter(handler)
    result = await client.evaluate(request)
    assert isinstance(result, JudgmentFailure) and result.code == "invalid_response"
    assert "PRIVATE" not in repr(result)
    assert len(calls) == 1
    await client.aclose()
    assert "PRIVATE" not in caplog.text + str(capsys.readouterr())
    assert "synthetic-credential-sentinel" not in caplog.text + repr(client) + repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": True, "output_tokens": "3"}, {"input_tokens": 0},
                                   {"output_tokens": 65536, "total_tokens": 9, "unused": "PRIVATE"}])
async def test_optional_usage_is_not_invented_or_a_failure(usage):
    request = prepared()
    data = answer(request)
    data["usage"] = usage
    client = adapter(lambda _: httpx.Response(200, json=data))
    result = await client.evaluate(request)
    assert not isinstance(result, JudgmentFailure)
    assert result.usage.total_tokens is None
    assert result.usage.input_tokens == (0 if usage == {"input_tokens": 0} else None)
    assert result.usage.output_tokens == (65536 if usage and "unused" in usage else None)
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [401, 429, 302, "timeout", "cancel"])
async def test_http_failure_timeout_cancel_never_retry_or_redirect(outcome):
    calls = []
    def handler(request):
        calls.append(request)
        if outcome == "timeout": raise httpx.ReadTimeout("PRIVATE")
        if outcome == "cancel": raise asyncio.CancelledError()
        return httpx.Response(outcome, headers={"Location": "https://elsewhere.invalid"}, content=b"PRIVATE")
    client = adapter(handler)
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError): await client.evaluate(prepared())
    else:
        result = await client.evaluate(prepared())
        assert result.code == ("provider_timeout" if outcome == "timeout" else "provider_error")
    assert len(calls) == 1
    if outcome != "cancel":
        await client.evaluate(prepared())
    assert len(calls) == (1 if outcome == "cancel" else 2)
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [0, 1])
async def test_streaming_body_cap_is_inclusive_and_closes_response(extra):
    request = prepared()
    data = answer(request)
    del data["usage"]
    body = json.dumps(data).encode()
    body += b" " * (65536 + extra - len(body))
    class Stream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            for index in range(0, len(body), 1000):
                yield body[index:index + 1000]
        async def aclose(self):
            self.closed = True
    stream = Stream()
    client = adapter(lambda _: httpx.Response(200, stream=stream))
    result = await client.evaluate(request)
    assert stream.closed
    if extra:
        assert result.code == "invalid_response"
    else:
        assert result.usage == Usage(None, None, None)
    await client.aclose()


@pytest.mark.asyncio
async def test_whole_operation_deadline_cancels_stalled_transport():
    cancelled = []
    async def handler(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    client = adapter(handler)
    assert (await client.evaluate(prepared(), remaining_seconds=0)).code == "provider_timeout"
    assert cancelled == []
    result = await client.evaluate(prepared(), remaining_seconds=0.01)
    assert result.code == "provider_timeout" and cancelled == [True]
    await client.aclose()


def prepared_v2(phase="detect", round_=0):
    from copyeditor.judgment_v2_batch import prepare_judgments as prepare_v2
    data = JudgmentInput(phase, "ja", "text", Background("", "", "private-tone", ""), "private-style",
        tuple(JudgmentBlock(i, "private-source", "private-context", "private-candidate", None) for i in (2, 4)))
    return prepare_v2(data, candidate_round=round_).requests[0]


def answer_v2(request):
    return {"model": "jev-1.13.0", "answers": {
        key: {"type": "Noul", "noul": 0.5} for key in json.loads(request)["questions"]}}


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,round_", [("detect", 0), ("verify", 1), ("verify", 2)])
async def test_v2_actual_bytes_and_complete_noul_results(phase, round_):
    request, calls = prepared_v2(phase, round_), []
    def handler(wire):
        calls.append(wire)
        assert wire.content == request
        assert b"private-tone" not in wire.content and b"private-style" not in wire.content
        return httpx.Response(200, json=answer_v2(request))
    client = adapter(handler)
    try:
        result = await client.evaluate(request)
        assert [block.ordinal for block in result.blocks] == [2, 4]
        expected = (("gate", 0.5),) if phase == "detect" else (("gate", 0.5), ("meaning", 0.5))
        assert all(block.probabilities == expected and block.choice is None for block in result.blocks)
        assert result.usage == Usage(None, None, None) and len(calls) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "extra", "duplicate", "nan", "infinity", "overflow", "negative",
    "above", "bool", "string", "choice", "lowercase", "extra_field", "model", "oversize", "output"])
async def test_v2_rejects_invalid_noul_without_retry_or_sensitive_output(kind, caplog, capsys):
    request, calls = prepared_v2("verify", 2), []
    data = answer_v2(request)
    key = "b0002.meaning"
    if kind == "missing": del data["answers"][key]
    if kind == "extra": data["answers"]["b9999.gate"] = data["answers"][key]
    if kind in ("negative", "above", "bool", "string"):
        data["answers"][key]["noul"] = {"negative": -0.1, "above": 1.1, "bool": True, "string": "0.5"}[kind]
    if kind in ("choice", "lowercase"):
        data["answers"][key]["type"] = "choice" if kind == "choice" else "noul"
    if kind == "extra_field": data["answers"][key]["private"] = "private-response"
    if kind == "model": data["model"] = "jev-latest"
    if kind == "output": data["usage"] = {"output_tokens": 65537}
    body = json.dumps(data).encode()
    if kind == "duplicate": body = body.replace(b'"noul": 0.5', b'"noul": 0.5, "noul": 0.5', 1)
    if kind in ("nan", "infinity", "overflow"):
        body = body.replace(b'0.5', {"nan": b'NaN', "infinity": b'Infinity', "overflow": b'1e999'}[kind], 1)
    if kind == "oversize": body = b" " * 65537
    def handler(wire):
        calls.append(wire)
        return httpx.Response(200, content=body)
    client = adapter(handler)
    try:
        result = await client.evaluate(request)
        assert isinstance(result, JudgmentFailure) and result.code == "invalid_response"
        assert len(calls) == 1
        sinks = caplog.text + str(capsys.readouterr()) + repr(result) + repr(client)
        assert not any(secret in sinks for secret in (
            "private-source", "private-context", "private-candidate", "private-response", "synthetic-credential-sentinel"))
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [302, 429, "timeout", "cancel"])
async def test_v2_http_failures_and_cancel_do_not_retry_or_redirect(outcome):
    request, calls = prepared_v2(), []
    def handler(wire):
        calls.append(wire)
        if outcome == "timeout": raise httpx.ReadTimeout("private-error")
        if outcome == "cancel": raise asyncio.CancelledError()
        return httpx.Response(outcome, headers={"Location": "https://elsewhere.invalid"})
    client = adapter(handler)
    try:
        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await client.evaluate(request)
        else:
            result = await client.evaluate(request)
            assert result.code == ("provider_timeout" if outcome == "timeout" else "provider_error")
        assert len(calls) == 1
    finally:
        await client.aclose()
    assert (await client.evaluate(request)).code == "provider_error" and len(calls) == 1
