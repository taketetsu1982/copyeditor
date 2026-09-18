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
