from time import monotonic

from . import html, preservation
from .lint import lint_response
from .metrics import Metrics
from .prompt import system_instruction
from .providers.base import GenerationInput, ProviderFailure
from .requests import ValidationError, parse_request
from .responses import nonblank, parse_generation, validate_final


class Service:
    def __init__(self, config, snapshot, provider_factory):
        self.config, self.snapshot, self.provider_factory = config, snapshot, provider_factory

    async def polish(self, arguments):
        return await self._run("polish_text", arguments)

    async def lint(self, arguments):
        return await self._run("lint_text", arguments)

    async def _run(self, tool, arguments):
        model = self.config["model"] if tool == "polish_text" else None
        meter = Metrics(monotonic(), model, self.config["pricing"])
        language, result, failure = None, None, None
        try:
            supplied = arguments.get("language", self.config["default_language"]) if type(arguments) is dict else None
            if type(supplied) is str and supplied in self.snapshot.languages:
                language = supplied
            request = parse_request(tool, arguments, self.config, self.snapshot)
            language = request.language
            rules = self.snapshot.languages[language]
            if tool == "lint_text":
                result = dict(status="ok", protected_terms_checked=0, preservation=None,
                              **lint_response(request.items[0].text, rules))
            else:
                if request.format == "html" and not html.analyze(request.items[0].text).accepted:
                    raise ValidationError("invalid_input", "text")
                result = await self._polish(request, rules, meter, "items" in arguments)
        except ValidationError as error:
            failure = (error.code, error.field)
        except Exception:
            failure = ("internal_error", None)
        finally:
            measured = meter.snapshot()
        metadata = dict(schema_version=1, language=language, rules_version=self.snapshot.rules_version,
                        common_version=self.snapshot.common_version, model=model,
                        **{key: value for key, value in measured.items() if key != "regeneration_attempted"})
        if failure is None:
            result.update(metadata)
            try:
                validate_final(result)
            except ValidationError as error:
                failure = (error.code, None)
            except Exception:
                failure = ("internal_error", None)
        if failure is not None:
            error = ValidationError(*failure)
            result = dict(**metadata, status="error", error=dict(code=error.code, message=str(error), field=error.field),
                          model_called=measured["model_calls"] > 0, regeneration_attempted=measured["regeneration_attempted"])
        result["latency_ms"] = meter.snapshot()["latency_ms"]
        try:
            validate_final(result)
        except Exception as error:
            code = error.code if isinstance(error, ValidationError) else "internal_error"
            result = dict(**{**metadata, "latency_ms": result["latency_ms"]}, status="error",
                          error=dict(code=code, message=str(ValidationError(code, None)), field=None),
                          model_called=measured["model_calls"] > 0, regeneration_attempted=measured["regeneration_attempted"])
        return result

    async def _polish(self, request, rules, meter, items_route):
        terms = preservation.request_terms(request.items, rules.protected_terms)
        instruction = system_instruction(self.snapshot.common_bytes.decode("utf-8"), rules.prose, terms)
        ratio = {key: self.config["length_ratio." + key] for key in ("min", "max")}
        provider = self.provider_factory()
        accepted = {}
        pending = request.items
        for attempt in range(2):
            call = meter.start_call()
            generated = await provider.generate(GenerationInput(pending, request.language, request.format, request.background, instruction))
            meter.record_usage(call, generated.usage)
            if isinstance(generated, ProviderFailure):
                raise ValidationError(generated.code, None)
            candidates = parse_generation(generated, pending)
            retry, html_failed = [], False
            for original, candidate in zip(pending, candidates):
                text = original.text if candidate.flag else candidate.text
                # HTML raw spans have a separate consumer; preservation compares the original strings.
                checked = preservation.check(original.text, text, rules.protected_terms, ratio,
                                             "text" if request.format == "html" else request.format)
                structure = request.format != "html" or html.same_structure(original.text, text)
                if checked.failed or not structure:
                    if attempt == 0:
                        retry.append(original)
                        continue
                    html_failed |= not structure
                    flag = dict(kind="rejected", reason="Preservation checks failed.", checks=list(checked.failed))
                    text = original.text
                else:
                    flag = {**candidate.flag, "checks": []} if candidate.flag else None
                accepted[original.id] = dict(id=original.id, text=text, flag=flag, regenerated=bool(attempt),
                                            protected_terms=list(checked.matched_terms))
            if html_failed:
                raise ValidationError("html_structure", None)
            if not retry:
                break
            pending = tuple(retry)
        items = [accepted[original.id] for original in request.items]
        if not all(nonblank(item["text"]) for item in items):
            raise ValidationError("invalid_response", None)
        if sum(len(item["text"]) for item in items) > 16000:
            raise ValidationError("output_limit", None)
        for item in items:
            item.update(lint_response(item["text"], rules))
        result = dict(status="ok", protected_terms_checked=len(terms), preservation={"length_ratio": ratio})
        if items_route:
            result["items"] = items
        else:
            result.update({key: value for key, value in items[0].items() if key != "id"})
        return result
