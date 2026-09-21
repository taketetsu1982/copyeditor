"""Internal generation-4 pipeline; public dispatch switches separately."""
import asyncio

from . import html, preservation
from .edit_generation import parse_generation
from .edit_protocol import validate_final
from .judged_budget import EditBudget
from .judgment import JudgmentBlock, JudgmentFailure, JudgmentInput, _probability
from .judgment_v2 import classify_detection, classify_verification, snapshot
from .language_detection import _prose
from .lint import lint_response
from .prompt import edit_system_instruction
from .providers.base import EditGenerationInput, ProviderFailure
from .requests import ValidationError
from .responses import nonblank, preservation_payload, valid
from .rewrite_budget import RewriteFailure
from .rewrite_response import BUDGET_MESSAGE


async def polish(service, request, judgment, metrics, *, items_route=False, registry=snapshot):
    config, ruleset = service.config, service.snapshot
    enabled = config["judgment.enabled"]
    selected = registry(config["judgment.policy_version"], config["judgment.thresholds_version"]) if enabled else None
    rules, budget = ruleset.languages[request.language], EditBudget(metrics, config)
    ratio = {key: config["length_ratio." + key] for key in ("min", "max")}
    ordinals = {item.id: i for i, item in enumerate(request.items, 1)}
    detections, accepted = {}, {}

    async def operation(role, method, value, **options):
        try:
            timeout = budget.timeout(role)
            with budget.call(role, **options) as slot:
                async with asyncio.timeout(timeout):
                    result = await method(value)
                if not options.get("estimation"):
                    budget.record_usage(role, slot, result.usage)
            return result
        except TimeoutError:
            budget.checkpoint(provider_error="provider_timeout")
        except (ValidationError, RewriteFailure):
            raise
        except Exception:
            budget.checkpoint(provider_error="provider_error")

    async def phase(pending, round_):
        name = "verify" if round_ else "detect"
        blocks = tuple(JudgmentBlock(ordinals[item.id], item.text, item.context,
                       accepted[item.id]["text"] if round_ else None, None) for item in pending)
        prepared = budget.plan(JudgmentInput(name, request.language, request.format, request.background, "", blocks),
                               candidate_round=round_)
        values = {}
        for wire, batch in zip(prepared.requests, prepared.plan.batches):
            result = await operation("judgment", judgment.evaluate, wire)
            if isinstance(result, JudgmentFailure):
                budget.checkpoint(provider_error=result.code)
            valid(result.model == "jev-1.13.0" and tuple(b.ordinal for b in result.blocks) == batch.ordinals)
            for block in result.blocks:
                valid(tuple(k for k, _ in block.probabilities) == (("gate", "meaning") if round_ else ("gate",)))
                values[block.ordinal] = {key: _probability(value) for key, value in block.probabilities}
            budget.checkpoint()
        return values

    def metadata():
        return dict(schema_version=4, judgment_enabled=enabled, degree=request.degree, language=request.language,
            rules_version=ruleset.rules_version, common_version=ruleset.common_version,
            policy_version=selected.policy["id"] if enabled else None,
            thresholds_version=selected.threshold["id"] if enabled else None,
            policy_hash=selected.policy_hash if enabled else None, thresholds_hash=selected.threshold_hash if enabled else None,
            **metrics.snapshot())

    failure = None
    try:
        valid(request.degree in ("polish", "rewrite") and metrics.meters["editing"].degree == request.degree)
        if request.format == "html" and not html.analyze(request.items[0].text).accepted:
            raise ValidationError("invalid_input", "text")
        no_prose = request.format == "html" and not nonblank(_prose(request.items[0].text))
        detected = await phase(() if no_prose else request.items, 0) if enabled else {}
        for item in request.items:
            detections[item.id] = (classify_detection(detected[ordinals[item.id]]["gate"], selected) if not no_prose else
                dict(status="not_run", reason="no_editable_prose", probability=None)) if enabled else None
            accepted[item.id] = dict(id=item.id, text=item.text, diagnosis=None, flag=None, regenerated=False,
                protected_terms=list(preservation.matched_terms(item.text, rules.protected_terms)), detection=detections[item.id])
        pending = tuple(item for item in request.items if not enabled or detections[item.id]["status"] == "eligible")
        provider = service.provider_factory() if pending else None
        size = 4 if request.degree == "rewrite" else max(1, len(pending))
        groups = tuple(pending[start:start + size] for start in range(0, len(pending), size))
        for attempt in range(2):
            if not pending:
                break
            retry = set()
            for group in groups:
                batch = tuple(item for item in group if item in pending)
                if not batch:
                    continue
                instruction = edit_system_instruction(ruleset.common_bytes.decode(), rules.prose,
                                                      preservation.request_terms(batch, rules.protected_terms), request.degree)
                data = EditGenerationInput(batch, request.language, request.format, request.background, instruction, request.degree)
                estimate = await operation("editing", provider.estimate_input, data, estimation=True)
                budget.checkpoint(provider_error=estimate.code if isinstance(estimate, ProviderFailure) else None)
                generated = await operation("editing", provider.generate, data, estimated_input=estimate, is_regeneration=bool(attempt))
                invalid, candidates = None, ()
                if not isinstance(generated, ProviderFailure):
                    try:
                        candidates = parse_generation(generated, batch, stage=request.degree)
                    except ValidationError as error:
                        invalid = error.code
                # Inspect received integrity and merged body size before measured reservation excess.
                overrun, budget.overrun = budget.overrun, False
                try:
                    budget.checkpoint(provider_error=generated.code if isinstance(generated, ProviderFailure) else None,
                                      finish=getattr(generated, "finish", None), validation_error=invalid)
                    for original, candidate in zip(batch, candidates):
                        checked = preservation.check(original.text, candidate.text, rules.protected_terms, ratio,
                                                     "text" if request.format == "html" else request.format)
                        flag = ({**candidate.flag, "checks": []} if candidate.flag else
                                dict(kind="rejected", reason="Preservation checks failed.", checks=list(checked.failed)) if checked.failed else None)
                        accepted[original.id].update(text=candidate.text, diagnosis=candidate.diagnosis,
                                                    flag=flag, regenerated=bool(attempt))
                        if not candidate.flag and (checked.failed or (enabled and candidate.text == original.text)):
                            retry.add(original.id)
                finally:
                    budget.overrun = overrun
            if (sum(len(item["text"]) for item in accepted.values()) > 16000
                    or sum(len(item["diagnosis"] or "") for item in accepted.values()) > 8192):
                raise ValidationError("output_limit", None)
            budget.checkpoint()
            verifying = tuple(item for item in pending if accepted[item.id]["text"] != item.text
                              and not (accepted[item.id]["flag"] and accepted[item.id]["flag"]["kind"] == "unfixable"))
            verified = await phase(verifying, attempt + 1) if enabled else {}
            for original in verifying if enabled else ():
                values = verified[ordinals[original.id]]
                if not classify_verification(detections[original.id]["probability"], values["gate"], values["meaning"], selected):
                    retry.add(original.id)
            pending = tuple(item for item in pending if item.id in retry)
        items = [{**accepted[item.id], **lint_response(accepted[item.id]["text"], rules)} for item in request.items]
        result = dict(metadata(), status="ok", protected_terms_checked=len(preservation.request_terms(request.items, rules.protected_terms)),
                      preservation=preservation_payload(ratio))
        del result["model_called"], result["regeneration_attempted"]
        result.update(items=items) if items_route else result.update({k: v for k, v in items[0].items() if k != "id"})
        validate_final(result, request.items, expected_enabled=enabled, registry=registry, format=request.format)
        budget.checkpoint()
        result["latency_ms"] = metrics.snapshot()["latency_ms"]
        return result
    except (ValidationError, RewriteFailure) as error:
        failure = (error.code, error.field)
    except Exception:
        failure = ("internal_error", None)
    try:
        budget.checkpoint(validation_error=failure[0])
    except RewriteFailure as error:
        code, field = error.code, failure[1] if error.code == failure[0] else None
    result = dict(metadata(), status="error", error=dict(code=code, field=field,
                  message=BUDGET_MESSAGE if code == "request_budget" else str(ValidationError(code, field))))
    validate_final(result, expected_enabled=enabled, registry=registry)
    return result
