"""Generation-four public editing and lint entry."""
from time import monotonic

from .edit_pipeline import polish
from .edit_protocol import validate_final
from .judged_metrics import EditMetrics
from .judgment_v2 import snapshot as judgment_snapshot
from .lint import lint_response
from .metrics import Metrics
from .requests import ValidationError, parse_edit_request


def error_payload(tool, config, rules, arguments, *, meter=None, error=None, language=None, registry=judgment_snapshot):
    editing = tool == "polish_text"
    degree = arguments.get("degree", "polish") if type(arguments) is dict else None
    degree = degree if type(degree) is str and degree in ("polish", "rewrite") else None
    enabled = editing and config["judgment.enabled"]
    meter = meter or (EditMetrics(monotonic(), config["model"], config["pricing"],
                                degree=degree or "polish", judgment_enabled=enabled) if editing else
                      Metrics(monotonic(), None, {}))
    error = error or ValidationError("internal_error")
    result = dict(status="error", schema_version=4, language=language,
                  rules_version=rules.rules_version, common_version=rules.common_version,
                  error=dict(code=error.code, message=str(error), field=error.field), **meter.snapshot())
    if editing:
        selected = registry(config["judgment.policy_version"], config["judgment.thresholds_version"]) if enabled else None
        result.update(degree=degree, judgment_enabled=enabled,
                      policy_version=selected.policy["id"] if enabled else None,
                      thresholds_version=selected.threshold["id"] if enabled else None,
                      policy_hash=selected.policy_hash if enabled else None,
                      thresholds_hash=selected.threshold_hash if enabled else None)
    else:
        result.update(model=None, model_called=False)
    validate_final(result, tool=tool, expected_enabled=enabled, registry=registry)
    return result


class EditService:
    def __init__(self, config, snapshot, provider_factory, judgment=None, *, registry=judgment_snapshot):
        self.config, self.snapshot, self.provider_factory = config, snapshot, provider_factory
        self.judgment, self.registry = judgment, registry

    async def polish(self, arguments):
        return await self._run("polish_text", arguments)

    async def lint(self, arguments):
        return await self._run("lint_text", arguments)

    async def _run(self, tool, arguments):
        editing, config = tool == "polish_text", self.config
        degree = arguments.get("degree", "polish") if type(arguments) is dict else None
        degree = degree if type(degree) is str and degree in ("polish", "rewrite") else "polish"
        enabled = editing and config["judgment.enabled"]
        meter = (EditMetrics(monotonic(), config["model"], config["pricing"], degree=degree, judgment_enabled=enabled)
                 if editing else Metrics(monotonic(), None, {}))
        if enabled:
            meter.meters["judgment"].price = config["judgment.pricing"].get(config["judgment.model"])
        language = None
        try:
            explicit = arguments.get("language") if type(arguments) is dict else None
            if type(explicit) is str and explicit in self.snapshot.languages:
                language = explicit
            request = parse_edit_request(tool, arguments, config, self.snapshot)
            language = request.language
            if editing:
                return await polish(self, request, self.judgment, meter, items_route="items" in arguments, registry=self.registry)
            result = dict(status="ok", schema_version=4, language=language, protected_terms_checked=0, preservation=None,
                          rules_version=self.snapshot.rules_version, common_version=self.snapshot.common_version,
                          model=None, **{key: value for key, value in meter.snapshot().items() if key != "regeneration_attempted"},
                          **lint_response(request.items[0].text, self.snapshot.languages[language]))
            validate_final(result, tool=tool)
            return result
        except Exception as error:
            return error_payload(tool, config, self.snapshot, arguments, meter=meter, language=language,
                                 error=error if isinstance(error, ValidationError) else None, registry=self.registry)
