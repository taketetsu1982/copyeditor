import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from copyeditor.config import load_config
from copyeditor.providers.base import GenerationResult, Usage
from copyeditor.rules import load_rules
from copyeditor.service import Service
from copyeditor.metrics import Metrics
from copyeditor.requests import parse_edit_request
from copyeditor.rewrite_service import rewrite


def environment(case, mode):
    if type(case["protected_terms"]) is not list:
        raise ValueError("Invalid benchmark protected terms")
    if mode not in ("fixture", "live"):
        raise ValueError("Invalid benchmark mode")
    if mode == "live":
        config = load_config()
    else:
        with TemporaryDirectory() as directory:
            config = load_config(Path(directory) / "config.yaml", {"GOOGLE_CLOUD_PROJECT": "fixture"})
    terms = config["protected_terms"] + (tuple(case["protected_terms"]) if mode == "fixture" else ())
    return config, load_rules(ROOT / "rules", Path("/etc/copyeditor/rules.d") if mode == "live" else None, terms)


class FixtureProvider:
    def __init__(self, text, *, no_issue=False):
        self.text, self.no_issue = text, no_issue

    async def estimate_input(self, request):
        return 0

    async def generate(self, request):
        if request.stage == "diagnose":
            return GenerationResult(json.dumps({"diagnoses": [dict(id=item.id,
                status="no_issue" if self.no_issue else "issue", expression=None if self.no_issue else item.text.strip()[:160],
                reason=None if self.no_issue else "Clarify the source expression.") for item in request.items]}), "stop", Usage(0, 0, 0))
        return GenerationResult(json.dumps({"items": [dict(id=item.id, text=self.text, flag=None) for item in request.items]}),
                                "stop", Usage(0, 0, 0))


async def call_api(prompt, options, context):
    case, mode = context["vars"], options.get("config", {}).get("mode", "fixture")
    config, snapshot = environment(case, mode)
    arguments = dict(text=case["bad"], language=case["language"], format=case["format"],
                     **{key: value for key, value in case["background"].items() if key in ("audience", "purpose", "tone", "message")})
    async def execute(factory):
        if case.get("degree", "polish") == "rewrite":
            meter = Metrics(monotonic(), config["model"], config["pricing"], degree="rewrite")
            request = parse_edit_request("polish_text", dict(arguments, degree="rewrite"), config, snapshot)
            return await rewrite(request, config, snapshot, factory, meter)
        return await Service(config, snapshot, factory).polish(arguments)
    if mode == "live":
        from copyeditor.providers.vertex import Vertex
        result = await execute(lambda: Vertex(config))
    else:
        # Service catches provider exceptions; retain attempted access so it cannot become a normal result.
        with patch("socket.socket", side_effect=RuntimeError("Fixture network access denied")) as network, \
                patch("socket.getaddrinfo", side_effect=RuntimeError("Fixture network access denied")) as dns:
            result = await execute(lambda: FixtureProvider(case["good"], no_issue=not case["must_change"]))
        if network.called or dns.called:
            raise RuntimeError("Fixture network access denied")
    return {"output": json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)}


def comparison_environment(case, mode, enabled):
    from copyeditor.judgment_config import resolve_judgment_config
    from copyeditor.judgment import _json_value
    base, snapshot = environment(case, mode)
    judgment = resolve_judgment_config(_json_value({k[9:]: v for k, v in base.values.items() if k.startswith('judgment.')} | {'enabled': enabled}),
                                      {'TYPESAFE_API_KEY': 'fixture-only'} if mode == 'fixture' else None)
    return {**base.values, **judgment.values}, snapshot, judgment.secrets.get('TYPESAFE_API_KEY')


async def compare_request(cases, degree, enabled, layout, mode, plans):
    import httpx
    from contextlib import ExitStack
    from copyeditor import judged_budget
    from copyeditor.judgment import ACTION_CRITERIA
    from copyeditor.providers.typesafe import TypeSafe
    config, snapshot, secret = comparison_environment(cases[0], mode, enabled)
    arguments = dict(language='ja', degree=degree, format=cases[0]['format'], **cases[0]['background'])
    if layout == 'text': arguments['text'] = cases[0]['bad']
    else: arguments['items'] = [dict(id=f'b{i:04}', text=c['bad'], context='') for i, c in enumerate(cases, 1)]
    outputs = {c['bad']: c['good'] for c in cases}
    class Editor(FixtureProvider):
        async def generate(self, data):
            if data.stage == 'diagnose': return await super().generate(data)
            return GenerationResult(json.dumps({'items': [dict(id=i.id, text=outputs[i.text], flag=None) for i in data.items]}), 'stop', Usage(0, 0, 0))
    def reply(wire):
        data = json.loads(wire.content)
        answers = {k: (dict(type='noul', noul=.9) if q['type'] == 'noul' else
                   dict(type='choice', choice='simplify_vocabulary', confidence=1,
                        probabilities={a: int(a == 'simplify_vocabulary') for a in ACTION_CRITERIA})) for k, q in data['questions'].items()}
        return httpx.Response(200, json=dict(model='jev-1.13.0', answers=answers, usage=dict(input_tokens=0, output_tokens=0)))
    prepare = judged_budget.prepare_judgments
    def observe(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        plans.append(dict(version=prepared.plan.version, phase=prepared.plan.phase,
                          batches=[b._asdict() for b in prepared.plan.batches]))
        return prepared
    adapter = None
    with ExitStack() as stack:
        stack.enter_context(patch.object(judged_budget, 'prepare_judgments', observe))
        if mode == 'fixture':
            network = stack.enter_context(patch('socket.socket', side_effect=RuntimeError('Fixture network denied')))
            dns = stack.enter_context(patch('socket.getaddrinfo', side_effect=RuntimeError('Fixture network denied')))
            factory = lambda: Editor('')
        else:
            from copyeditor.providers.vertex import Vertex
            factory = lambda: Vertex(config)
        try:
            if enabled: adapter = TypeSafe(secret, timeout_ms=config['judgment.timeout_ms'], transport=httpx.MockTransport(reply) if mode == 'fixture' else None)
            response = await Service(config, snapshot, factory, adapter).polish(arguments)
            if mode == 'fixture' and (network.called or dns.called): raise RuntimeError('Fixture attempted network')
            return response
        finally:
            if adapter: await adapter.aclose()
