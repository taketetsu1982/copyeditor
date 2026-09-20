import asyncio

from . import html, preservation
from .diagnosis import parse_diagnoses
from .lint import lint_response
from .prompt import system_instruction
from .providers.base import GenerationInput, ProviderFailure
from .requests import ValidationError
from .responses import preservation_payload, parse_generation
from .rewrite_budget import RewriteBudget, RewriteFailure
from .rewrite_response import BUDGET_MESSAGE, validate_rewrite_final


async def _operation(method, data, budget):
    try:
        async with asyncio.timeout(budget.remaining):
            return await method(data)
    except (TimeoutError, asyncio.CancelledError):
        budget.checkpoint(provider_error="provider_timeout")
    except Exception:
        budget.checkpoint(provider_error="provider_error")


async def _generate(provider, data, budget, parser, *, retry=False):
    budget.checkpoint()
    estimate = await _operation(provider.estimate_input, data, budget)
    if isinstance(estimate, ProviderFailure):
        budget.checkpoint(provider_error=estimate.code)
    call = budget.start_call(estimate, is_regeneration=retry)
    generated = await _operation(provider.generate, data, budget)
    budget.record_usage(call, generated.usage)
    if isinstance(generated, ProviderFailure):
        budget.checkpoint(provider_error=generated.code)
    parsed, error = None, None
    try:
        parsed = parser(generated)
    except ValidationError as failure:
        error = failure.code
    budget.checkpoint(finish=generated.finish, validation_error=error)
    return parsed


async def rewrite(request, config, snapshot, provider_factory, meter, *, items_route=False):
    """Process a validated rewrite request using its tool-entry metrics clock."""
    budget = RewriteBudget(meter, meter.clock)
    rules = snapshot.languages[request.language]
    ratio = {key: config["length_ratio." + key] for key in ("min", "max")}
    result, failure = None, None
    try:
        budget.checkpoint()
        if request.degree != "rewrite" or meter.degree != "rewrite":
            raise ValidationError("internal_error")
        if request.format == "html" and not html.analyze(request.items[0].text).accepted:
            raise ValidationError("invalid_input", "text")
        terms = preservation.request_terms(request.items, rules.protected_terms)
        def instruction(stage):
            return system_instruction(snapshot.common_bytes.decode("utf-8"), rules.prose, terms, stage)
        provider = provider_factory()
        data = GenerationInput(request.items, request.language, request.format, request.background,
                               instruction("diagnose"), "diagnose")
        diagnoses = await _generate(provider, data, budget, lambda value: parse_diagnoses(value, request.items))
        fixed = {item.id: item for item in diagnoses}
        rewrite_instruction = instruction("rewrite")
        accepted = {}
        for start in range(0, len(request.items), 4):
            pending = request.items[start:start + 4]
            for attempt in range(2):
                data = GenerationInput(pending, request.language, request.format, request.background,
                                       rewrite_instruction, "rewrite", tuple(fixed[item.id] for item in pending))
                candidates = await _generate(provider, data, budget,
                    lambda value: parse_generation(value, pending, data.diagnoses), retry=bool(attempt))
                retry, html_failed = [], False
                for original, candidate in zip(pending, candidates):
                    text = original.text if candidate.flag else candidate.text
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
                        protected_terms=list(checked.matched_terms), diagnosis=fixed[original.id].diagnosis._asdict())
                budget.checkpoint(validation_error="html_structure" if html_failed else None)
                if not retry:
                    break
                pending = tuple(retry)
        items = [accepted[original.id] for original in request.items]
        if sum(len(item["text"]) for item in items) > 16000:
            budget.checkpoint(validation_error="output_limit")
        for item in items:
            item.update(lint_response(item["text"], rules))
        result = dict(status="ok", protected_terms_checked=len(terms), preservation=preservation_payload(ratio))
        if items_route:
            result["items"] = items
        else:
            result.update({key: value for key, value in items[0].items() if key != "id"})
    except (RewriteFailure, ValidationError) as error:
        try:
            budget.checkpoint(validation_error=error.code)
        except RewriteFailure as terminal:
            failure = (terminal.code, error.field if terminal.code == error.code else None)
    except Exception:
        try:
            budget.checkpoint(validation_error="internal_error")
        except RewriteFailure as terminal:
            failure = (terminal.code, None)
    measured = meter.snapshot()
    metadata = dict(schema_version=2, degree="rewrite", language=request.language, model=config["model"],
                    rules_version=snapshot.rules_version, common_version=snapshot.common_version,
                    **{key: value for key, value in measured.items() if key != "regeneration_attempted"})
    if failure is None:
        result.update(metadata)
        error = None
        try:
            budget.checkpoint()
            result["latency_ms"] = meter.snapshot()["latency_ms"]
            validate_rewrite_final(result, request.items)
        except ValidationError as invalid:
            error = invalid.code
        except RewriteFailure as expired:
            error = expired.code
        except Exception:
            error = "internal_error"
        try:
            budget.checkpoint(validation_error=error)
        except RewriteFailure as terminal:
            failure = (terminal.code, None)
    if failure is not None:
        code, field = failure
        metadata["latency_ms"] = meter.snapshot()["latency_ms"]
        result = dict(**metadata, status="error", error=dict(code=code, field=field,
            message=BUDGET_MESSAGE if code == "request_budget" else str(ValidationError(code, field))),
            model_called=measured["model_calls"] > 0, regeneration_attempted=measured["regeneration_attempted"])
    return result
