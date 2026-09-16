from pathlib import Path
import runpy
import shutil
import pytest
from copyeditor.config import load_config, strict_yaml
from copyeditor.html import same_structure
from copyeditor.preservation import check
from copyeditor.rules import load_rules

ROOT = Path(__file__).resolve().parents[2]
SECTIONS = ("vocabulary", "syntax", "structure", "translation", "context")
load_examples = runpy.run_path(str(ROOT / "scripts/examples_to_promptfoo.py"))["load_examples"]


def assets(root):
    for language in ("en", "zh"):
        path = root / "rules" / (language + ".md")
        assert path.is_file()
        raw = path.read_text(encoding="utf-8")
        assert strict_yaml(raw.split("---", 2)[1])["native_reviewed"] is False
        assert f"# {language} writing rules\nDraft: not reviewed by a native speaker.\n" in raw
        assert set(SECTIONS) <= {p.stem for p in (root / "examples" / language).glob("*.yaml")}
    snapshot = load_rules(root / "rules", overlay=None)
    cases = load_examples(root / "examples", snapshot)
    return snapshot, [c for c in cases if c["language"] in ("en", "zh")]


@pytest.mark.consumer("CTR-03")
@pytest.mark.consumer("CTR-05")
def test_ctr03_ctr05_en_zh_assets_preserve_content(tmp_path):
    snapshot, cases = assets(ROOT)
    config = load_config(tmp_path / "absent", {"GOOGLE_CLOUD_PROJECT": "fixture-project"})
    ratio = {key: config["length_ratio." + key] for key in ("min", "max")}
    for case in cases:
        rules = snapshot.languages[case["language"]]
        assert rules.detectors
        terms = (*rules.protected_terms, *case["protected_terms"])
        format = "text" if case["format"] == "html" else case["format"]
        assert not check(case["bad"], case["good"], terms, ratio, format).failed, case["id"]
        if case["format"] == "html":
            assert same_structure(case["bad"], case["good"]), case["id"]


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("missing", ["rules", "directory", "empty", *SECTIONS])
def test_ctr03_ctr05_missing_en_zh_assets_fail(tmp_path, language, missing):
    for directory in ("rules", "examples"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    examples = tmp_path / "examples" / language
    if missing == "rules":
        (tmp_path / "rules" / (language + ".md")).unlink()
    elif missing == "directory":
        shutil.rmtree(examples)
    elif missing == "empty":
        for path in examples.glob("*.yaml"):
            path.unlink()
    else:
        (examples / (missing + ".yaml")).unlink()
    with pytest.raises(AssertionError):
        assets(tmp_path)
