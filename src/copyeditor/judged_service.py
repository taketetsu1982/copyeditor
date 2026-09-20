import asyncio
import json

from . import html, preservation
from .diagnosis import parse_diagnoses
from .judgment import JudgmentInput, JudgmentBlock, JudgmentFailure, classify_detection, classify_verification, _registry, definition_hash
from .judged_budget import JudgedBudget
from .judged_response import validate_judged_final
from .lint import lint_response
from .prompt import system_instruction
from .providers.base import GenerationInput, ProviderFailure
from .requests import ValidationError
from .responses import preservation_payload, parse_generation, valid
from .rewrite_budget import RewriteFailure
from .rewrite_response import BUDGET_MESSAGE


async def polish(service, request, judgment, metrics, *, items_route=False):
    return await _run(service, request, judgment, metrics, items_route=items_route)


async def rewrite(service, request, judgment, metrics, *, items_route=False):
    return await _run(service, request, judgment, metrics, items_route=items_route)


async def _run(service, request, judgment, metrics, *, items_route=False):
    """Process a validated request with tool-entry metrics and a shared judgment adapter."""
    config, snapshot = service.config, service.snapshot
    budget = JudgedBudget(metrics, config)
    policy_id, threshold_id = config["judgment.policy_version"], config["judgment.thresholds_version"]
    policy, threshold = _registry(policy_id, threshold_id)
    versions = dict(policy_id=policy_id, threshold_id=threshold_id)
    rules = snapshot.languages[request.language]
    ratio = {key: config["length_ratio." + key] for key in ("min", "max")}
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
    async def phase(name, blocks):
        data = JudgmentInput(name, request.language, request.format, request.background, request.background.tone, tuple(blocks))
        prepared = budget.plan(data)
        classified = {}
        for wire, batch in zip(prepared.requests, prepared.plan.batches):
            result = await operation("judgment", judgment.evaluate, wire)
            if isinstance(result, JudgmentFailure):
                budget.checkpoint(provider_error=result.code)
            valid(result.model == config["judgment.model"] and tuple(b.ordinal for b in result.blocks) == batch.ordinals)
            classify = classify_detection if name == "detect" else classify_verification
            classified.update((block.ordinal, classify(block, **versions)) for block in result.blocks)
            budget.checkpoint()
        return classified
    def metadata():
        measured = metrics.snapshot()
        return dict(schema_version=3, degree=request.degree, language=request.language, rules_version=snapshot.rules_version,
                    common_version=snapshot.common_version, policy_version=policy_id, thresholds_version=threshold_id,
                    policy_hash=definition_hash(policy), thresholds_hash=definition_hash(threshold), **measured)
    failure = None
    try:
        valid(request.degree == metrics.meters["editing"].degree and request.degree in ("polish", "rewrite"))
        analysis = html.analyze(request.items[0].text) if request.format == "html" else None
        if analysis is not None and not analysis.accepted:
            raise ValidationError("invalid_input", "text")
        no_prose = analysis is not None and None not in analysis.signature
        detections = ({1: dict(status="not_run", reason="no_editable_prose", gate=None, checks=[], action=None)} if no_prose else
            await phase("detect", (JudgmentBlock(i, item.text, item.context, None, None) for i, item in enumerate(request.items, 1))))
        eligible = tuple(item for i, item in enumerate(request.items, 1) if detections[i]["status"] == "eligible")
        actions = {item.id: detections[i]["action"]["effective"] for i, item in enumerate(request.items, 1) if item in eligible}
        accepted, fixed = {}, {}
        if eligible:
            budget.checkpoint()
            provider = service.provider_factory()
            async def generate(pending, attempt, stage="polish"):
                terms = preservation.request_terms(pending, rules.protected_terms)
                instruction = system_instruction(snapshot.common_bytes.decode("utf-8"), rules.prose, terms, stage)
                instruction += "\nApply only each target's selected action and necessary surrounding wording:\n" + json.dumps([
                    dict(id=item.id, action=actions[item.id], instruction=policy["action_instructions"][actions[item.id]]) for item in pending], ensure_ascii=False)
                if stage == "diagnose":
                    instruction += "\nFor each target, diagnose only its selected action: quote one exact expression and give one sentence of reason. " \
                                   "Choose no_issue when no such change is needed. An action never permits inventing information."
                diagnoses = tuple(fixed[item.id] for item in pending) if stage == "rewrite" else ()
                data = GenerationInput(pending, request.language, request.format, request.background, instruction, stage, diagnoses)
                estimate = await operation("editing", provider.estimate_input, data, estimation=True)
                budget.checkpoint(provider_error=estimate.code if isinstance(estimate, ProviderFailure) else None)
                generated = await operation("editing", provider.generate, data, estimated_input=estimate, is_regeneration=bool(attempt))
                invalid = None
                if not isinstance(generated, ProviderFailure):
                    try:
                        if stage == "diagnose":
                            parse_diagnoses(generated, pending)
                        else:
                            parse_generation(generated, pending, diagnoses or None)
                    except ValidationError as error:
                        invalid = error.code
                # Preservation/HTML checks must run before selecting a measured overrun.
                overrun, budget.overrun = budget.overrun, False
                try:
                    budget.checkpoint(provider_error=generated.code if isinstance(generated, ProviderFailure) else None,
                                      finish=getattr(generated, "finish", None), validation_error=invalid)
                finally:
                    budget.overrun = overrun
                return generated
            targets = eligible
            if request.degree == "rewrite":
                diagnosed = await generate(eligible, 0, "diagnose")
                fixed = {item.id: item for item in parse_diagnoses(diagnosed, eligible)}
                budget.checkpoint()
                targets = tuple(item for item in eligible if fixed[item.id].diagnosis.status == "issue")
            async def candidate(pending, attempt):
                return await generate(pending, attempt, request.degree)
            size = 4 if request.degree == "rewrite" else len(eligible)
            for start in range(0, len(targets), size):
                edited = await service._polish(request._replace(items=targets[start:start + size]), rules, None, True, generate=candidate)
                accepted.update((item["id"], item) for item in edited["items"])
                budget.checkpoint()
        candidates = [(i, original, accepted[original.id]) for i, original in enumerate(request.items, 1)
                      if original.id in accepted and accepted[original.id]["flag"] is None]
        verified = await phase("verify", (JudgmentBlock(i, original.text, original.context, item["text"], actions[original.id])
                                          for i, original, item in candidates)) if candidates else {}
        items = []
        for i, original in enumerate(request.items, 1):
            item = accepted.get(original.id, dict(id=original.id, text=original.text, flag=None, regenerated=False,
                protected_terms=list(preservation.matched_terms(original.text, rules.protected_terms))))
            verification = verified.get(i, dict(status="not_run", checks=[], reason=(
                "unfixable" if item["flag"]["kind"] == "unfixable" else "preservation_rejected") if item["flag"] else "not_generated"))
            if i in verified and verification["status"] != "pass":
                item.update(text=original.text, flag=dict(kind="verification_rejected",
                    reason="Candidate verification failed." if verification["status"] == "fail" else "Candidate verification was inconclusive.",
                    checks=[check["id"] for check in verification["checks"] if check["result"] != "pass"]))
            diagnosis = fixed[original.id].diagnosis._asdict() if original.id in fixed else None
            editing = "generated" if original.id in accepted else "diagnosed_no_issue" if diagnosis else "not_run"
            item.update(diagnosis=diagnosis, editing=editing,
                        detection=detections[i], verification=verification, **lint_response(item["text"], rules))
            items.append(item)
        result = dict(metadata(), status="ok", protected_terms_checked=len(preservation.request_terms(request.items, rules.protected_terms)),
                      preservation=preservation_payload(ratio))
        del result["model_called"], result["regeneration_attempted"]
        result.update(items=items) if items_route else result.update({key: value for key, value in items[0].items() if key != "id"})
        validate_judged_final(result, request.items, format=request.format)
        result["latency_ms"] = metrics.snapshot()["latency_ms"]
        budget.checkpoint()
        return result
    except (ValidationError, RewriteFailure) as error:
        failure = (error.code, error.field)
    except Exception:
        failure = ("internal_error", None)
    try:
        budget.checkpoint(validation_error=failure[0])
    except RewriteFailure as terminal:
        code, field = terminal.code, failure[1] if terminal.code == failure[0] else None
    result = dict(metadata(), status="error", error=dict(code=code, field=field,
        message=BUDGET_MESSAGE if code == "request_budget" else str(ValidationError(code, field))))
    validate_judged_final(result)
    result["latency_ms"] = metrics.snapshot()["latency_ms"]
    return result
