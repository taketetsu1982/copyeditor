"""Freeze and retain synthetic rewrite trials; human quality judgments remain separate."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

from benchmark_provider import ROOT, call_api, environment
from examples_to_promptfoo import load_examples
from copyeditor.rules import load_rules
from copyeditor.edit_protocol import validate_final
from copyeditor.providers.base import SourceItem
from copyeditor import html, preservation
from copyeditor.prompt import edit_system_instruction as system_instruction

FIXED = [f"rewrite-{i:02}" for i in range(1, 25)]
THRESHOLDS = dict(examples=24, problem=18, natural=6, repeats=5, per_example=4, per_repeat=15,
                  meaning_violations=0, unnecessary_changes=0, natural_failures=0)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    os.replace(temporary, path)


def fixtures():
    return {case["id"]: case for case in load_examples(ROOT / "examples", load_rules(ROOT / "rules", None))
            if case["language"] == "ja" and case["degree"] == "rewrite"}


def freeze(revision, mode="fixture"):
    if type(revision) is not int or revision < 1 or mode not in ("fixture", "live"):
        raise ValueError("Invalid evaluation plan")
    cases = fixtures()
    if not set(FIXED) <= cases.keys():
        raise ValueError("Missing fixed examples")
    groups = {"acceptance": FIXED, "regression": sorted(cases.keys() - set(FIXED))}
    entries = []
    for group, ids in groups.items():
        for identity in ids:
            case = cases[identity]
            config, snapshot = environment(case, mode)
            path = f"examples/ja/{identity}.yaml"
            entries.append(dict(group=group, id=identity, path=path, sha256=digest((ROOT / path).read_bytes()),
                repeats=5, must_change=case["must_change"], model=config["model"], thinking=config["thinking"],
                rules_version=snapshot.rules_version, common_version=snapshot.common_version, instruction_hashes={stage: digest(system_instruction(
                    snapshot.common_bytes.decode(), snapshot.languages["ja"].prose, preservation.request_terms(
                        (SourceItem("text", case["bad"], ""),), snapshot.languages["ja"].protected_terms), stage).encode())
                    for stage in ("rewrite",)}))
    return dict(revision=revision, mode=mode, source_commit=subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), thresholds=THRESHOLDS.copy(), entries=entries,
        prompt_hash=digest((ROOT / "src/copyeditor/prompt.py").read_bytes()))


def check_plan(plan):
    if plan != freeze(plan["revision"], plan["mode"]):
        raise ValueError("Evaluation inputs changed; freeze and review a new plan")


def batch_plan():
    return dict(status="unmeasured", repeats=3, variants=["single-candidate-batch", "four-item-batches"],
        failures=["api", "timeout", "safety", "truncated", "invalid_id"],
        controls=["model", "prompt", "rules", "config"],
        records=["stage", "failure_code", "model_calls", "usage", "cost", "latency_ms", "cause_unidentified"],
        conditions=[dict(items=n, body=body, context=context, valid=context <= n * 1000,
            reason=None if context <= n * 1000 else "Per-item context limit prevents this total")
            for n in (1, 4, 5, 16, 32) for body in (1000, 6000, 12000) for context in (0, 4000)])


async def run(plan, output):
    check_plan(plan)
    cases = fixtures()
    artifact = dict(plan=json.loads(json.dumps(plan)), started_at=datetime.now(timezone.utc).isoformat(),
        trials=[dict(group=e['group'], id=e['id'], repeat=r, response=None, error='not_run',
                     judgment=dict(a=None, b=None, c=None, d=None, reason=None))
                for e in plan['entries'] for r in range(1, e['repeats'] + 1)], batch_plan=batch_plan())
    save(output, artifact)
    for trial in artifact['trials']:
        response, error, cancelled = None, None, False
        try:
            reply = await call_api("", {"config": {"mode": plan["mode"]}}, {"vars": cases[trial["id"]]})
            response = json.loads(reply["output"])
        except (Exception, asyncio.CancelledError) as failure:
            error = "evaluation_error"
            cancelled = isinstance(failure, asyncio.CancelledError)
        trial.update(response=response, error=error or (response.get("error", {}).get("code") if response else "evaluation_error"))
        save(output, artifact)
        if cancelled:
            raise asyncio.CancelledError
    return artifact


def summarize(plan, artifact):
    check_plan(plan)
    if artifact["plan"] != plan:
        raise ValueError("Evaluation plan was replaced")
    expected = {(e["group"], e["id"], r) for e in plan["entries"] for r in range(1, e["repeats"] + 1)}
    trials = artifact["trials"]
    actual = [(t["group"], t["id"], t["repeat"]) for t in trials]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Incomplete or moved evaluation trials")
    cases, scores, failures, pending = fixtures(), {}, [], []
    for trial in trials:
        case, response, judgment = cases[trial["id"]], trial["response"], trial["judgment"]
        key = (trial["group"], trial["id"], trial["repeat"])
        required = ("a", "b", "c") if case["must_change"] else ("b", "c", "d")
        if set(judgment) != {"a", "b", "c", "d", "reason"}:
            raise ValueError("Invalid human judgment")
        unused = "d" if case["must_change"] else "a"
        if judgment[unused] is not None or any(judgment[k] is not None and type(judgment[k]) is not bool for k in required):
            raise ValueError("Invalid human judgment")
        if any(judgment[k] is None for k in required) or not isinstance(judgment["reason"], str) or not judgment["reason"].strip():
            pending.append(key)
        healthy = isinstance(response, dict) and response.get("status") == "ok" and not trial["error"]
        changed = healthy and response.get("text") != case["bad"]
        try:
            if response is None or trial["error"] == "evaluation_error":
                raise ValueError()
            entry = next(e for e in plan["entries"] if e["id"] == trial["id"])
            if any(response.get(k) != entry[k] for k in ("rules_version", "common_version")):
                raise ValueError()
            if response['degree'] != 'rewrite' or response['language'] != case['language']: raise ValueError()
            if response["providers"][0]["model"] != entry["model"]: raise ValueError()
            validate_final(response, (SourceItem("text", case["bad"], ""),), format=case["format"])
            if healthy:
                config, snapshot = environment(case, plan["mode"])
                ratio = {k: config["length_ratio." + k] for k in ("min", "max")}
                if preservation.check(case["bad"], response["text"], snapshot.languages["ja"].protected_terms,
                        ratio, "text" if case["format"] == "html" else case["format"]).failed:
                    raise ValueError()
            elif response["error"]["code"] in ("invalid_response", "html_structure"):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            failures.append(key)
        if judgment["b"] is False or judgment["c"] is False or (not case["must_change"] and (not healthy or response.get("flag") or changed or judgment["d"] is False)):
            failures.append(key)
        scores[key] = healthy and response.get("flag") is None and (changed if case["must_change"] else not changed) and all(judgment[k] is True for k in required)
    group_results = {}
    for group in ("acceptance", "regression"):
        keys = [k for k in scores if k[0] == group]
        group_results[group] = dict(planned=len(keys), passed=sum(scores[k] for k in keys),
            rate=sum(scores[k] for k in keys) / len(keys) if keys else None)
    problem = [e["id"] for e in plan["entries"] if e["group"] == "acceptance" and e["must_change"]]
    rates = len(problem) == 18 and all(sum(scores["acceptance", i, r] for r in range(1, 6)) >= 4 for i in problem)
    rates = rates and all(sum(scores["acceptance", i, r] for i in problem) >= 15 for r in range(1, 6))
    passed = not failures and not pending and rates
    return dict(status="unreviewed" if pending else "criteria_met" if passed else "failed", groups=group_results,
        must_failures=len(set(failures)), pending_judgments=len(pending),
        not_run=sum(t["error"] == "not_run" for t in trials), criteria_met=passed,
        quality_accepted=passed and plan["mode"] == "live")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "run", "report"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, default=Path("rewrite-evaluation.json"))
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    args = parser.parse_args()
    if args.operation == "plan":
        save(args.plan, freeze(args.revision, args.mode))
        return
    plan = json.loads(args.plan.read_text())
    if args.mode != plan["mode"]:
        parser.error("Mode must match the frozen plan; live execution requires --mode live")
    if args.operation == "run":
        asyncio.run(run(plan, args.artifact))
    else:
        artifact = json.loads(args.artifact.read_text())
        result = summarize(plan, artifact)
        artifact["summary"] = result
        save(args.artifact, artifact)
        args.artifact.with_suffix(".md").write_text("# Rewrite evaluation\n\n" + json.dumps(result, indent=2) + "\n\n" +
            "\n".join(f"- {t['group']}/{t['id']}/{t['repeat']}: {json.dumps(t['judgment'], ensure_ascii=False)}" for t in artifact["trials"]) + "\n")
        print(result["status"])
        if not result["criteria_met"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
