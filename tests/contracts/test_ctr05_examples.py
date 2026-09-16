import runpy
import json
from pathlib import Path
import subprocess
import sys
import pytest
from copyeditor.rules import CATEGORIES, load_rules
from .test_ctr03_rules import document
ROOT = Path(__file__).resolve().parents[2]
converter = runpy.run_path(str(ROOT / "scripts/examples_to_promptfoo.py"))
@pytest.fixture
def assets(tmp_path):
    base, examples = tmp_path / "rules", tmp_path / "examples"
    base.mkdir()
    (base / "common.md").write_text("Common")
    (base / "ja.md").write_text(document())
    (examples / "ja").mkdir(parents=True)
    for section in CATEGORIES:
        case = dict(id=section, language="ja", section=section, bad="Product bad.", good="Product good.", reason="Retain Product.", lint={"rule_ids": ["ja-vocabulary-001"]}, protected_terms=["Product"], regression="protected-terms-overreach")
        (examples / "ja" / (section + ".yaml")).write_text(json.dumps(case))
    return base, examples
def test_ctr05_defaults_conversion_and_order(assets, tmp_path):
    base, examples = assets
    cases = converter["load_examples"](examples, load_rules(base, None))
    assert [c["id"] for c in cases] == sorted(CATEGORIES)
    assert all(c["format"] == "text" and c["background"] == {} and c["must_change"] for c in cases)
    output = tmp_path / "config.json"
    converter["convert"](output, cases)
    result = json.loads(output.read_text())
    assert [t["vars"] for t in result["tests"]] == cases and result["providers"][0]["config"]["mode"] == "fixture"
    assert (output.parent / result["providers"][0]["id"][7:]).resolve() == ROOT / "scripts/benchmark_provider.py"
@pytest.mark.parametrize("change", [dict(extra=1), dict(id="wrong"), dict(language="en"), dict(reason=None), dict(background={"other": "x"}), dict(protected_terms=["x", "x"]), dict(must_change=1), dict(lint={"rule_ids": []}), dict(good="Product bad."), dict(format="unknown")])
def test_ctr05_invalid_schema(assets, change):
    base, examples = assets
    path = examples / "ja/vocabulary.yaml"
    path.write_text(json.dumps(json.loads(path.read_text()) | change))
    with pytest.raises(ValueError, match="^Invalid examples$"):
        converter["load_examples"](examples, load_rules(base, None))
@pytest.mark.parametrize("missing", ["section", "lint", "all"])
def test_ctr05_coverage_is_required(assets, missing):
    base, examples = assets
    for path in (examples / "ja").iterdir():
        if missing == "all" or (missing == "section" and path.stem == "context"): path.unlink()
        elif missing == "lint": path.write_text(json.dumps(json.loads(path.read_text()) | {"lint": None}))
    with pytest.raises(ValueError):
        converter["load_examples"](examples, load_rules(base, None))
if any(p.name != "README.md" for p in (ROOT / "examples").iterdir()) or any(p.name not in ("common.md", "README.md") for p in (ROOT / "rules").iterdir()):
    @(pytest.mark.consumer("CTR-05") if any(p.name != "README.md" for p in (ROOT / "examples").iterdir()) else lambda f: f)
    def test_ctr05_installed_assets(tmp_path):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/examples_to_promptfoo.py"), "--output", str(tmp_path / "promptfoo.json")], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
