import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from copyeditor.config import load_config
from copyeditor.providers.base import GenerationResult, Usage
from copyeditor.rules import load_rules
from copyeditor.service import Service


def environment(case, mode):
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
    def __init__(self, text):
        self.text = text

    async def generate(self, request):
        return GenerationResult(json.dumps({"items": [dict(id=item.id, text=self.text, flag=None) for item in request.items]}),
                                "stop", Usage(0, 0, 0))


async def call_api(prompt, options, context):
    case, mode = context["vars"], options.get("config", {}).get("mode", "fixture")
    config, snapshot = environment(case, mode)
    arguments = dict(text=case["bad"], language=case["language"], format=case["format"],
                     **{key: value for key, value in case["background"].items() if key in ("audience", "purpose", "tone", "message")})
    if mode == "live":
        from copyeditor.providers.vertex import Vertex
        result = await Service(config, snapshot, lambda: Vertex(config)).polish(arguments)
    else:
        # Service catches provider exceptions; retain attempted access so it cannot become a normal result.
        with patch("socket.socket", side_effect=RuntimeError("Fixture network access denied")) as network, \
                patch("socket.getaddrinfo", side_effect=RuntimeError("Fixture network access denied")) as dns:
            result = await Service(config, snapshot, lambda: FixtureProvider(case["good"])).polish(arguments)
        if network.called or dns.called:
            raise RuntimeError("Fixture network access denied")
    return {"output": json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)}
