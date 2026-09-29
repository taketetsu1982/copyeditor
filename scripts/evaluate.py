"""Evaluate authorized external documents; never bundle private examples in this repository."""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

TEXT_LIMIT = 20000
READER_LIMIT = 500
MAX_DOCUMENTS = 50
MAX_RUNS = 10
# ASCII words and numbers stand in for the names, sources, labels and figures a rewrite must keep.
PROTECTED = re.compile(r"[A-Za-z][A-Za-z0-9&@.\-]*[A-Za-z0-9]|[A-Za-z]\d|\d[\d,.]*(?:%|/\d+)?")
MARKDOWN_LINK = re.compile(r"\[([^\]\n]+)\]\([^)\n]*\)")
HTML_LINK = re.compile(r"<a\b[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
TAG = re.compile(r"<[^>]+>")
DESU_MASU = re.compile(r"(?:です|ます|ません|でした|ました)(?=。|$)", re.MULTILINE)


def nonblank(value, limit):
    if not isinstance(value, str) or not 1 <= len(value) <= limit or not value.strip():
        return False
    value.encode("utf-8")
    return True


def validate_documents(documents):
    if not isinstance(documents, list) or not 1 <= len(documents) <= MAX_DOCUMENTS:
        raise ValueError("Expected 1 to 50 external documents.")
    seen = set()
    for document in documents:
        if not isinstance(document, dict) or set(document) - {"id", "text", "reader"}:
            raise ValueError("Invalid external document.")
        identifier = document.get("id")
        if (not nonblank(identifier, 100) or identifier in seen or not nonblank(document.get("text"), TEXT_LIMIT)
                or ("reader" in document and not nonblank(document["reader"], READER_LIMIT))):
            raise ValueError("Invalid external document.")
        seen.add(identifier)
    return documents


def prose_chars(text):
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = TAG.sub("", MARKDOWN_LINK.sub(r"\1", text))
    text = re.sub(r"(?m)^[\s|:\-]+$", "", text)
    text = re.sub(r"(?m)^\s*(?:#+|[-*+]|\d+\.|>)\s+(?:\[[ xX]\]\s+)?|\*\*|\|", "", text)
    return len(re.sub(r"\s", "", text))


def nonblank_lines(text):
    return sum(1 for line in text.splitlines() if line.strip())


def link_texts(text):
    found = set(MARKDOWN_LINK.findall(text)) | {TAG.sub("", inner).strip() for inner in HTML_LINK.findall(text)}
    return {value for value in found if value}


def metrics(original, output):
    base = prose_chars(original)
    return {
        "prose_ratio": round(prose_chars(output) / base, 3) if base else None,
        "line_ratio": round(nonblank_lines(output) / nonblank_lines(original), 3),
        "lost_tokens": sorted(set(PROTECTED.findall(original)) - set(PROTECTED.findall(output))),
        "lost_link_texts": sorted(link_texts(original) - link_texts(output)),
        "desu_masu_delta": len(DESU_MASU.findall(output)) - len(DESU_MASU.findall(original)),
        "fullwidth_paren_delta": output.count("（") - original.count("（"),
    }


def summarize(documents, rows, runs):
    summary = []
    for document in documents:
        done = [row for row in rows if row["id"] == document["id"] and row["result"] == "success"]
        ratios = [row["metrics"]["prose_ratio"] for row in done if row["metrics"]["prose_ratio"] is not None]
        summary.append(dict(id=document["id"], runs=runs, successes=len(done),
                            prose_ratio_min=min(ratios, default=None), prose_ratio_max=max(ratios, default=None),
                            lost_tokens=[len(row["metrics"]["lost_tokens"]) for row in done],
                            lost_link_texts=[len(row["metrics"]["lost_link_texts"]) for row in done]))
    return summary


async def evaluate(documents, provider, runs=3, progress=None, clock=time.monotonic):
    from copyeditor.providers.vertex import ProviderFailure
    validate_documents(documents)
    if not 1 <= runs <= MAX_RUNS:
        raise ValueError("Expected 1 to 10 runs.")
    rows = []
    for document in documents:
        for run in range(1, runs + 1):
            row = dict(id=document["id"], run=run, result="model_error", seconds=None, retries=0, usage={},
                       text=None, metrics=None)
            started = clock()
            try:
                generation = await provider.polish(document["text"], document.get("reader"))
                row.update(result="success", retries=generation.retries, usage=generation.usage,
                           text=generation.text, metrics=metrics(document["text"], generation.text))
            except ProviderFailure as error:
                row.update(retries=error.retries, usage=error.usage)
            except Exception:
                pass
            row["seconds"] = round(clock() - started, 1)
            rows.append(row)
            if progress is not None:
                progress({key: row[key] for key in ("id", "run", "result")})
    return dict(runs=runs, documents=summarize(documents, rows, runs), results=rows)


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
    from copyeditor.providers.vertex import DEFAULT_READER, SYSTEM_INSTRUCTION, USER_TEMPLATE, Vertex
    documents_bytes = args.input.read_bytes()
    documents = validate_documents(json.loads(documents_bytes))
    config = load_config()
    provider = Vertex(config)
    try:
        report = await evaluate(documents, provider, args.runs, lambda value: print(json.dumps(value), flush=True))
    finally:
        await provider.aclose()
    instruction = "\n".join((SYSTEM_INSTRUCTION, USER_TEMPLATE, DEFAULT_READER))
    report.update(model=config["model"], location=config["vertex.location"],
                  input_sha256=hashlib.sha256(documents_bytes).hexdigest(),
                  instruction_sha256=hashlib.sha256(instruction.encode()).hexdigest())
    atomic_write(args.output, report)
    print(json.dumps(report["documents"], ensure_ascii=False), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3, help="Generations per document (1-10).")
    parser.add_argument("--truststore", action="store_true", help="Use OS certificates for this evaluation only.")
    args = parser.parse_args()
    from copyeditor.auth_boundary import disable_library_logging
    disable_library_logging()
    try:
        if args.input.resolve() == args.output.resolve() or not 1 <= args.runs <= MAX_RUNS:
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
