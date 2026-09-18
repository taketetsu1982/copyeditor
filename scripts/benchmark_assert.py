import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_provider import environment
from copyeditor import html, preservation
from copyeditor.lint import lint
from copyeditor.responses import validate_final
from copyeditor.rewrite_response import validate_rewrite_final
from copyeditor.providers.base import SourceItem


def get_assert(output, context):
    try:
        case, mode = context["vars"], context.get("config", {}).get("mode", "fixture")
        config, snapshot = environment(case, mode)
        result = json.loads(output)
        if case.get("degree", "polish") == "rewrite":
            validate_rewrite_final(result, (SourceItem("text", case["bad"], ""),))
        else:
            validate_final(result)
        if result["status"] != "ok" or result["flag"] is not None or result["language"] != case["language"]:
            return False
        text, rules = result["text"], snapshot.languages[case["language"]]
        ratio = {key: config["length_ratio." + key] for key in ("min", "max")}
        if preservation.check(case["bad"], text, rules.protected_terms, ratio, "text" if case["format"] == "html" else case["format"]).failed:
            return False
        if case["format"] == "html" and not html.same_structure(case["bad"], text):
            return False
        if (case["must_change"] and text == case["bad"]) or (mode == "fixture" and text != case["good"]):
            return False
        if case.get("degree") == "rewrite" and not case["must_change"] and text != case["bad"]:
            return False
        declared = set(case["lint"]["rule_ids"]) if case["lint"] else set()
        fixture_rules = environment(case, "fixture")[1].languages[case["language"]]
        return declared <= {f.rule_id for f in lint(case["bad"], fixture_rules)} and not declared & {f.rule_id for f in lint(case["good"], fixture_rules)}
    except (ValueError, TypeError, KeyError):
        return False
