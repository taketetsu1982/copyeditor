from typing import Literal, NamedTuple, Protocol
class SourceItem(NamedTuple):
    id: str
    text: str
    context: str
class Background(NamedTuple):
    audience: str
    purpose: str
    tone: str
    message: str
class EditGenerationInput(NamedTuple):
    items: tuple[SourceItem, ...]
    language: str
    format: str
    background: Background
    system_instruction: str
    stage: Literal["polish", "rewrite"] = "polish"
class Usage(NamedTuple):
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
class GenerationResult(NamedTuple):
    raw_json: str | None
    finish: str
    usage: Usage
class ProviderFailure(NamedTuple):
    code: str
    usage: Usage
class Provider(Protocol):
    async def estimate_input(self, input: EditGenerationInput) -> int | ProviderFailure: ...
    async def generate(self, input: EditGenerationInput) -> GenerationResult | ProviderFailure: ...
