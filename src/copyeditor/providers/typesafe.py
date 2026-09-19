import asyncio
import json
import math

import httpx

from ..config import ConfigError
from ..judgment import JudgmentBlockResult, JudgmentFailure, JudgmentResult, _probability, validate_choice
from ..responses import reject_constant, unique_object, valid
from .base import Usage


def _finite_float(value):
    result = float(value)
    valid(math.isfinite(result))
    return result


def _usage(data):
    values = data.get("usage") if type(data) is dict else None
    values = values if type(values) is dict else {}
    return Usage(*(v if type(v) is int and v >= 0 else None
                   for v in (values.get("input_tokens"), values.get("output_tokens"))), None)


def _parse(data, request):
    valid(type(data) is dict and {"model", "answers"} <= set(data) <= {"model", "answers", "usage"})
    valid(data["model"] == "jev-1.13.0")
    answers, questions = data["answers"], request["questions"]
    valid(type(answers) is dict and set(answers) == set(questions))
    blocks = {}
    for key, question in questions.items():
        block_id, predicate = key.split(".")
        ordinal = int(block_id[1:])
        probabilities, choice = blocks.setdefault(ordinal, ([], None))
        answer = answers[key]
        valid(type(answer) is dict and answer.get("type") == question["type"])
        if question["type"] == "noul":
            valid(set(answer) == {"type", "noul"})
            probabilities.append((predicate, _probability(answer["noul"])))
        else:
            valid(set(answer) == {"type", "choice", "probabilities", "confidence"})
            choice = validate_choice(answer["choice"], answer["probabilities"], answer["confidence"])
        blocks[ordinal] = (probabilities, choice)
    return tuple(JudgmentBlockResult(ordinal, tuple(probabilities), choice)
                 for ordinal, (probabilities, choice) in sorted(blocks.items()))


class TypeSafe:
    def __init__(self, secret, *, timeout_ms=10000, transport=None):
        self._secret, self._stopped = secret, False
        self.timeout = timeout_ms / 1000
        failed = False
        try:
            self._client = httpx.AsyncClient(
                transport=transport if transport is not None else httpx.AsyncHTTPTransport(retries=0),
                follow_redirects=False, timeout=self.timeout, trust_env=False)
        except Exception:
            failed = True
        if failed:
            raise ConfigError("credentials_unavailable", "judgment.credentials")

    async def evaluate(self, request: bytes, *, remaining_seconds=None):
        usage = Usage(None, None, None)
        if self._stopped:
            return JudgmentFailure("provider_error", usage)
        code = "provider_error"
        try:
            expected = json.loads(request)
            valid(expected["model"] == "jev-1.13.0")
            timeout = self.timeout if remaining_seconds is None else min(self.timeout, remaining_seconds)
            if timeout <= 0:
                return JudgmentFailure("provider_timeout", usage)
            async with asyncio.timeout(timeout):
                async with self._client.stream("POST", "https://api.typesafe.ai/v1/systemone", content=request,
                                               headers={"Authorization": "Bearer " + self._secret.reveal(),
                                                        "Content-Type": "application/json"}) as response:
                    if not 200 <= response.status_code < 300:
                        return JudgmentFailure(code, usage)
                    code = "invalid_response"
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        valid(len(body) + len(chunk) <= 65536)
                        body.extend(chunk)
                    data = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object,
                                      parse_constant=reject_constant, parse_float=_finite_float)
                    usage = _usage(data)
                    valid(usage.output_tokens is None or usage.output_tokens <= 65536)
                    blocks = _parse(data, expected)
                    return JudgmentResult("jev-1.13.0", blocks, usage)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            code = "provider_timeout"
        except httpx.HTTPError:
            code = "provider_error"
        except Exception:
            pass
        return JudgmentFailure(code, usage)

    async def aclose(self):
        self._stopped = True
        try:
            await self._client.aclose()
        except Exception:
            pass
