import argparse
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from copyeditor.config import array, matches, nonblank, strict_yaml
from copyeditor.lint import lint
from copyeditor.rules import CATEGORIES, load_rules, require
def load_examples(root, snapshot):
    try:
        cases, coverage, sections = [], set(), set()
        require(not Path(root).is_symlink())
        for directory in sorted(Path(root).iterdir()):
            if directory.name == "README.md": continue
            require(not directory.is_symlink() and directory.is_dir() and directory.name in snapshot.languages)
            rules = snapshot.languages[directory.name]
            ids = {r["id"] for r in rules.detectors}
            for path in sorted(directory.iterdir()):
                require(not path.is_symlink() and path.is_file() and path.suffix == ".yaml")
                data = strict_yaml(path.read_text(encoding="utf-8"))
                required = {"id", "language", "section", "bad", "good", "reason", "lint"}
                require(isinstance(data, dict) and required <= data.keys() <= required | {"format", "background", "protected_terms", "must_change", "regression"})
                data = dict(format="text", background={}, protected_terms=[], must_change=True) | data
                require(matches(r"[a-z][a-z0-9-]{0,63}", data["id"]) and data["id"] == path.stem and data["language"] == directory.name and data["section"] in CATEGORIES)
                require(all(nonblank(data[k], limit) for k, limit in (("bad", 12000), ("good", 16000), ("reason", 1000))))
                require(data["format"] in ("text", "markdown", "html") and type(data["must_change"]) is bool and (not data["must_change"] or data["bad"] != data["good"]))
                background = data["background"]
                require(isinstance(background, dict) and background.keys() <= {"audience", "purpose", "tone", "message"} and all(isinstance(v, str) and len(v) <= 1000 for v in background.values()))
                require(array(data["protected_terms"], 1024, lambda s: nonblank(s, 128)) and len(set(rules.protected_terms) | set(data["protected_terms"])) <= 2048)
                require("regression" not in data or data["regression"] == "protected-terms-overreach")
                declared = data["lint"]
                if declared is not None:
                    require(isinstance(declared, dict) and set(declared) == {"rule_ids"} and array(declared["rule_ids"], 256, lambda id: id in ids) and declared["rule_ids"])
                    bad, good = ({f.rule_id for f in lint(data[key], rules)} for key in ("bad", "good"))
                    require(set(declared["rule_ids"]) <= bad and not set(declared["rule_ids"]) & good)
                    coverage.update(declared["rule_ids"])
                json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
                sections.add((data["language"], data["section"]))
                cases.append(data)
        require(cases and {(lang, section) for lang in snapshot.languages for section in CATEGORIES} <= sections)
        require({r["id"] for rules in snapshot.languages.values() for r in rules.detectors} <= coverage)
        if "ja" in snapshot.languages:
            require(any(c["language"] == "ja" and c.get("regression") == "protected-terms-overreach" and c["must_change"] and c["protected_terms"] and c["bad"].strip() not in c["protected_terms"] and all(t in c["bad"] and t in c["good"] for t in c["protected_terms"]) for c in cases))
        return sorted(cases, key=lambda c: (c["language"], c["id"]))
    except Exception:
        raise ValueError("Invalid examples") from None
def convert(output, cases, live=False):
    require(bool(cases))
    relative = lambda name: "file://" + Path(os.path.relpath(ROOT / "scripts" / name, Path(output).resolve().parent)).as_posix()
    config = dict(prompts=["{{bad}}"], providers=[dict(id=relative("benchmark_provider.py"), config=dict(mode="live" if live else "fixture"))],
                  tests=[dict(description=c["language"] + "/" + c["id"], vars=c, **{"assert": [dict(type="python", value=relative("benchmark_assert.py"), config=dict(mode="live" if live else "fixture"))]}) for c in cases])
    Path(output).write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    convert(args.output, load_examples(ROOT / "examples", load_rules(ROOT / "rules", None)), args.live)
