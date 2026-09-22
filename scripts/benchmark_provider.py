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
from copyeditor.config import ResolvedConfig, freeze
from copyeditor.judgment_config import JudgmentSecret
from copyeditor.judgment_v2 import POLICY_ID, snapshot as judgment_snapshot


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
        return GenerationResult(json.dumps({"items": [dict(id=item.id, text=self.text, flag=None,
            diagnosis=None if request.stage == "polish" else "No expression change needed." if self.no_issue else "Clearer wording.")
            for item in request.items]}), "stop", Usage(0, 0, 0))


async def call_api(prompt, options, context):
    case, mode = context["vars"], options.get("config", {}).get("mode", "fixture")
    config, snapshot = environment(case, mode)
    arguments = dict(text=case["bad"], language=case["language"], format=case["format"],
                     **{key: value for key, value in case["background"].items() if key in ("audience", "purpose", "tone", "message")})
    arguments["degree"] = case.get("degree", "polish")
    async def execute(factory):
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


# Offline fixtures explicitly inject their registry; production never registers these values.
FIXTURE_THRESHOLDS = {"synthetic": dict(id="synthetic", floor=0.5, gap=0.2, meaning_floor=0.8)}


def fixture_registry(policy, threshold):
    return judgment_snapshot(policy, threshold, thresholds=FIXTURE_THRESHOLDS, pairs={(POLICY_ID, "synthetic")})


def comparison_environment(case, mode, enabled):
    base, snapshot = environment(case, mode)
    values = dict(base.values, **{"judgment.enabled": enabled})
    if mode == "fixture":
        values.update({"judgment.policy_version": POLICY_ID, "judgment.thresholds_version": "synthetic" if enabled else None})
        secret = JudgmentSecret("fixture-only") if enabled else None
    else:
        if enabled:
            judgment_snapshot(values["judgment.policy_version"], values["judgment.thresholds_version"])
        secret = base.secrets.get("TYPESAFE_API_KEY") if enabled else None
    return values, snapshot, secret


async def compare_request(cases, degree, enabled, layout, mode, plans):
    import httpx
    from contextlib import ExitStack
    from copyeditor.judged_budget import EditBudget
    from copyeditor.providers.typesafe import TypeSafe
    config, snapshot, secret = comparison_environment(cases[0], mode, enabled)
    arguments = dict(language='ja', degree=degree, format=cases[0]['format'], **cases[0]['background'])
    if layout == 'text': arguments['text'] = cases[0]['bad']
    else: arguments['items'] = [dict(id=f'b{i:04}', text=c['bad'], context='') for i, c in enumerate(cases, 1)]
    outputs = {c['bad']: c['good'] for c in cases}
    class Editor(FixtureProvider):
        async def generate(self, data):
            return GenerationResult(json.dumps({'items': [dict(id=i.id, text=outputs[i.text], flag=None,
                diagnosis=None if degree == 'polish' else 'Clearer wording.') for i in data.items]}), 'stop', Usage(0, 0, 0))
    def reply(wire):
        data = json.loads(wire.content)
        checking = 'originals' in data['state']
        answers = {key: dict(type='Noul', noul=.9 if not checking or key.endswith('meaning') else .3)
                   for key in data['questions']}
        return httpx.Response(200, json=dict(model='jev-1.13.0', answers=answers, usage=dict(input_tokens=0, output_tokens=0)))
    prepare = EditBudget.plan
    def observe(self, data, **kwargs):
        prepared = prepare(self, data, **kwargs)
        plans.append(dict(version=prepared.plan.version, phase=prepared.plan.phase,
                          candidate_round=kwargs['candidate_round'], batches=[b._asdict() for b in prepared.plan.batches]))
        return prepared
    adapter = provider = None
    with ExitStack() as stack:
        stack.enter_context(patch.object(EditBudget, 'plan', observe))
        if mode == 'fixture':
            network = stack.enter_context(patch('socket.socket', side_effect=RuntimeError('Fixture network denied')))
            dns = stack.enter_context(patch('socket.getaddrinfo', side_effect=RuntimeError('Fixture network denied')))
            factory = lambda: Editor('')
        else:
            from copyeditor.providers.vertex import Vertex
            provider = Vertex(config)
            factory = lambda: provider
        try:
            if enabled: adapter = TypeSafe(secret, timeout_ms=config['judgment.timeout_ms'], transport=httpx.MockTransport(reply) if mode == 'fixture' else None)
            config = ResolvedConfig(freeze(config), {})
            response = await Service(config, snapshot, factory, adapter,
                registry=fixture_registry if mode == 'fixture' else judgment_snapshot).polish(arguments)
            if mode == 'fixture' and (network.called or dns.called): raise RuntimeError('Fixture attempted network')
            return response
        finally:
            if adapter: await adapter.aclose()
            if provider: await provider.aclose()
