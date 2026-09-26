"""Evaluate authorized external cases; never bundle private examples in this repository."""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

TIERS = ("\u5909\u3048\u306a\u3044", "\u30ae\u30ea\u5909\u3048\u306a\u3044",
         "\u30ae\u30ea\u5909\u3048\u308b", "\u5909\u3048\u308b", "AI\u81ed\u3059\u304e\u308b")


def validate_cases(cases):
    if not isinstance(cases, list) or len(cases) != 15:
        raise ValueError("Expected 15 external cases, three per tier.")
    for case in cases:
        if (not isinstance(case, dict) or case.get("tier") not in TIERS
                or not isinstance(case.get("sent_text"), str) or not case["sent_text"].strip()
                or not 1 <= len(case["sent_text"]) <= 12000):
            raise ValueError("Invalid external case.")
        case["sent_text"].encode("utf-8")
    if Counter(case["tier"] for case in cases) != Counter({tier: 3 for tier in TIERS}):
        raise ValueError("Expected three cases per tier.")
    return cases


async def evaluate(cases, provider, progress=None):
    from copyeditor.providers.vertex import ProviderFailure
    validate_cases(cases)
    rows = []
    for index, case in enumerate(cases, 1):
        expected = case["tier"] not in TIERS[:2]
        row = dict(index=index, tier=case["tier"], expected_change=expected,
                   result="model_error", changed=None, matched=False, text=None, retries=0, usage={})
        try:
            generation = await provider.polish(case["sent_text"])
            changed = generation.text != case["sent_text"]
            row.update(result="success", changed=changed, matched=changed == expected,
                       text=generation.text, retries=generation.retries, usage=generation.usage)
        except ProviderFailure as error:
            row.update(retries=error.retries, usage=error.usage)
        except Exception:
            pass
        rows.append(row)
        if progress is not None:
            progress({key: row[key] for key in ("index", "result", "matched")})
    matches = sum(row["matched"] for row in rows)
    unchanged = sum(row["matched"] for row in rows if row["tier"] == TIERS[0])
    return dict(matches=matches, total=15, unchanged_tier_matches=unchanged,
                passed=matches >= 9 and unchanged == 3,
                by_tier={tier: sum(row["matched"] for row in rows if row["tier"] == tier) for tier in TIERS},
                cases=rows)


def atomic_write(path, report):
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def run(args):
    from copyeditor.config import load_config
    from copyeditor.providers.vertex import SYSTEM_INSTRUCTION, Vertex
    cases_bytes = args.input.read_bytes()
    cases = validate_cases(json.loads(cases_bytes))
    config = load_config()
    provider = Vertex(config)
    try:
        report = await evaluate(cases, provider, lambda value: print(json.dumps(value), flush=True))
    finally:
        await provider.aclose()
    report.update(model=config["model"], location=config["vertex.location"],
                  input_sha256=hashlib.sha256(cases_bytes).hexdigest(),
                  prompt_sha256=hashlib.sha256(SYSTEM_INSTRUCTION.encode()).hexdigest())
    atomic_write(args.output, report)
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}), flush=True)
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--truststore", action="store_true", help="Use OS certificates for this evaluation only.")
    args = parser.parse_args()
    from copyeditor.auth_boundary import disable_library_logging
    disable_library_logging()
    try:
        if args.input.resolve() == args.output.resolve():
            raise ValueError()
        if args.truststore:
            import truststore
            truststore.inject_into_ssl()
        return asyncio.run(run(args))
    except Exception:
        print("Evaluation failed. Check input, configuration, ADC, and local TLS trust.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
