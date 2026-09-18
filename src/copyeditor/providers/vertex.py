import asyncio
import google.auth
import httpx
from google import genai
from google.auth.transport.requests import Request
from google.genai import types
from copyeditor.config import ConfigError
from copyeditor.prompt import contents
from .base import GenerationResult, ProviderFailure, Usage
RESPONSE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "text", "flag"], "properties": {
        "id": {"type": "string"}, "text": {"type": "string"}, "flag": {"anyOf": [{"type": "null"}, {"type": "object",
        "additionalProperties": False, "required": ["kind", "reason"], "properties": {"kind": {"const": "unfixable"}, "reason": {"type": "string"}}}]}}}}}}
DIAGNOSIS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["diagnoses"], "properties": {
    "diagnoses": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["id", "status", "expression", "reason"], "properties": {
            "id": {"type": "string"}, "status": {"enum": ["issue", "no_issue"]},
            "expression": {"type": ["string", "null"]}, "reason": {"type": ["string", "null"]}}}}}}
def usage(response):
    metadata = getattr(response, "usage_metadata", None)
    values = [getattr(metadata, key, None) for key in ("prompt_token_count", "candidates_token_count", "thoughts_token_count", "total_token_count")]
    prompt, candidate, thought, total = [v if type(v) is int and v >= 0 else None for v in values]
    return Usage(prompt, candidate + thought if candidate is not None and thought is not None else None, total)
async def validate_count_response(response):
    # SDK integer coercion must not turn a boolean/string estimate into a reservation.
    if response.request.url.path.endswith(":countTokens") and response.is_success:
        await response.aread()
        data = response.json()
        value = data.get("totalTokens") if type(data) is dict else None
        if type(value) is not int or value < 0:
            raise ValueError()
class Vertex:
    def __init__(self, config):
        self.config = config
        try:
            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            if not credentials.valid:
                credentials.refresh(Request())
            if not credentials.valid:
                raise ValueError()
            self.client = genai.Client(vertexai=True, project=config["vertex.project"], location=config["vertex.location"], credentials=credentials,
                http_options=types.HttpOptions(timeout=60000, retry_options=types.HttpRetryOptions(attempts=1),
                                               async_client_args={"transport": httpx.AsyncHTTPTransport(retries=0),
                                                                  "event_hooks": {"response": [validate_count_response]}}))
        except Exception:
            raise ConfigError("credentials_unavailable", "credentials") from None
    def options(self, input):
        if input.stage not in ("polish", "diagnose", "rewrite"):
            raise ValueError()
        if input.stage == "rewrite":
            if [item.id for item in input.diagnoses] != [item.id for item in input.items]:
                raise ValueError()
        elif input.diagnoses:
            raise ValueError()
        return types.GenerateContentConfig(system_instruction=input.system_instruction, temperature=0, max_output_tokens=8192,
            response_mime_type="application/json", response_json_schema=DIAGNOSIS_SCHEMA if input.stage == "diagnose" else RESPONSE_SCHEMA,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel(self.config["thinking"].upper())))
    async def estimate_input(self, input):
        try:
            options = self.options(input)
            generation = types.GenerationConfig(**options.model_dump(exclude_none=True, exclude={"system_instruction"}))
            async with asyncio.timeout(60):
                response = await self.client.aio.models.count_tokens(model=self.config["model"], contents=contents(input),
                    config=types.CountTokensConfig(system_instruction=input.system_instruction, generation_config=generation))
            estimate = response.total_tokens
            if type(estimate) is not int or estimate < 0:
                raise ValueError()
            return estimate
        except (TimeoutError, httpx.TimeoutException):
            return ProviderFailure("provider_timeout", Usage(None, None, None))
        except Exception:
            return ProviderFailure("provider_error", Usage(None, None, None))
    async def generate(self, input):
        response = None
        try:
            async with asyncio.timeout(60):
                response = await self.client.aio.models.generate_content(model=self.config["model"], contents=contents(input),
                    config=self.options(input))
            candidate = (response.candidates or [None])[0]
            reason = getattr(candidate, "finish_reason", None)
            blocked = reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION")
            blocked = blocked or getattr(getattr(response, "prompt_feedback", None), "block_reason", None) not in (None, "BLOCK_REASON_UNSPECIFIED")
            finish = "blocked" if blocked else {"STOP": "stop", "MAX_TOKENS": "truncated"}.get(reason, "other")
            parts = getattr(getattr(candidate, "content", None), "parts", None) or []
            texts = [p.text for p in parts if p.text is not None and not p.thought]
            return GenerationResult("".join(texts) if texts else None, finish, usage(response))
        except (TimeoutError, httpx.TimeoutException) as error:
            return ProviderFailure("provider_timeout", usage(response if response is not None else error))
        except Exception as error:
            return ProviderFailure("provider_error", usage(response if response is not None else error))
    async def aclose(self):
        await self.client.aio.aclose()
        self.client.close()
