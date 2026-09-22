import hashlib

import json

import math

from collections.abc import Mapping

from types import MappingProxyType

from typing import Literal, NamedTuple

from .providers.base import Background, Usage

from .responses import valid

def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value

def _json_value(value):
    if isinstance(value, Mapping):
        return {key: _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    return value

def definition_hash(definition):
    payload = json.dumps(_json_value(definition), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

Phase = Literal["detect", "verify"]

class JudgmentBlock(NamedTuple):
    ordinal: int
    source: str
    context: str
    candidate: str | None
    action: None

class JudgmentInput(NamedTuple):
    phase: Phase
    language: str
    format: Literal["text", "markdown", "html"]
    background: Background
    desired_style: str
    blocks: tuple[JudgmentBlock, ...]

class JudgmentBlockResult(NamedTuple):
    ordinal: int
    probabilities: tuple[tuple[str, float], ...]
    choice: None

class JudgmentResult(NamedTuple):
    model: str
    blocks: tuple[JudgmentBlockResult, ...]
    usage: Usage

class JudgmentFailure(NamedTuple):
    code: Literal["provider_error", "provider_timeout", "invalid_response"]
    usage: Usage

class JudgmentBatch(NamedTuple):
    ordinals: tuple[int, ...]
    input_units: int
    state_question_units: int

def _probability(value):
    valid(type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value))
    return value
