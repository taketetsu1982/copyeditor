from pathlib import Path
import runpy
import shutil
import pytest
from copyeditor.config import load_config
from copyeditor.html import same_structure
from copyeditor.preservation import check
from copyeditor.rules import load_rules

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = {"vocabulary", "syntax", "structure", "translation", "context", "protected-terms-overreach"}
load_examples = runpy.run_path(str(ROOT / "scripts/examples_to_promptfoo.py"))["load_examples"]


def assets(root):
    assert (root / "rules/ja.md").is_file()
    assert EXAMPLES <= {p.stem for p in (root / "examples/ja").glob("*.yaml")}
    snapshot = load_rules(root / "rules", overlay=None)
    cases = [c for c in load_examples(root / "examples", snapshot) if c["language"] == "ja"]
    assert cases and snapshot.languages["ja"].detectors
    return snapshot.languages["ja"], cases


@pytest.mark.consumer("CTR-03")
@pytest.mark.consumer("CTR-05")
def test_ac_04_1_ctr03_ctr05_ja_assets_preserve_content(tmp_path):
    rules, cases = assets(ROOT)
    config = load_config(tmp_path / "absent", environ={"GOOGLE_CLOUD_PROJECT": "fixture-project"})
    ratio = {key: config["length_ratio." + key] for key in ("min", "max")}
    for case in cases:
        terms = (*rules.protected_terms, *case["protected_terms"])
        format = "text" if case["format"] == "html" else case["format"]
        assert not check(case["bad"], case["good"], terms, ratio, format).failed, case["id"]
        if case["format"] == "html":
            assert same_structure(case["bad"], case["good"]), case["id"]
        if case.get("regression") == "protected-terms-overreach":
            assert case["must_change"] and case["bad"] != case["good"]


def test_ac_04_5_ctr05_lp_protects_name_without_freezing_prose(tmp_path):
    _, cases = assets(ROOT)
    case = next(c for c in cases if c["id"] == "protected-terms-overreach")
    terms = case["protected_terms"]
    assert terms and all(t in case["bad"] and t in case["good"] for t in terms)
    config = load_config(tmp_path / "absent", environ={"GOOGLE_CLOUD_PROJECT": "fixture-project"})
    ratio = {key: config["length_ratio." + key] for key in ("min", "max")}
    assert "protected_terms" in check(case["bad"], case["good"], [case["bad"]], ratio).failed
    assert "protected_terms" in check(case["bad"], case["good"].replace(terms[0], ""), terms, ratio).failed
    assert not same_structure(case["bad"], case["good"].replace("<p>", "<div>"))


@pytest.mark.parametrize("missing", ["rules/ja.md", "examples/ja", "empty", *["examples/ja/" + name + ".yaml" for name in sorted(EXAMPLES)]])
def test_ac_04_1_ctr03_ctr05_missing_ja_assets_fail(tmp_path, missing):
    for directory in ("rules", "examples"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    if missing == "empty":
        for path in (tmp_path / "examples/ja").glob("*.yaml"):
            path.unlink()
    else:
        path = tmp_path / missing
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    with pytest.raises(AssertionError):
        assets(tmp_path)
