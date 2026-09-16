import hashlib
import json
from pathlib import Path
import pytest
from copyeditor.config import ConfigError
from copyeditor.rules import HEADINGS, load_rules

DETECTORS = [{"kind": "literal", "value": "bad"}, {"kind": "regex", "pattern": "bad+"},
    {"kind": "sentence_length", "max": 10, "terminators": "."}, {"kind": "comma_count", "max": 0, "commas": ",", "terminators": "."},
    {"kind": "repeated_ending", "endings": ["end"], "min_run": 2, "terminators": "."}, {"kind": "brackets", "pairs": ["()", "[]"]},
    {"kind": "width_mix", "half": "a", "full": "ａ"}]
def document(detector=None, number="001", term="Base", empty=False):
    rule = dict(id=f"ja-vocabulary-{number}", section="vocabulary", description="Prefer direct wording.", detector=detector or DETECTORS[0])
    prose = ["" if empty else "Change awkward words; retain meaning. bad: 冗長 good: 明快 reason: Be direct." for _ in range(5)]
    prose[0] += f'\n<a id="{rule["id"]}"></a>'
    data = dict(schema_version=1, protected_terms=[term], rules=[rule])
    return "---\nlanguage: ja\nrevision: 1\nnative_reviewed: true\n---\n# ja writing rules\n" + "\n".join(
        "## " + heading + "\n" + content for heading, content in zip(HEADINGS, [*prose, "```json\n" + json.dumps(data) + "\n```\n"]))
@pytest.fixture
def tree(tmp_path):
    base, overlay = tmp_path / "base", tmp_path / "overlay"
    base.mkdir()
    (base / "common.md").write_bytes(b"Common\r\n")
    (base / "ja.md").write_text(document())
    return base, overlay
@pytest.mark.parametrize("value", DETECTORS, ids=[d["kind"] for d in DETECTORS])
def test_ctr03_detector_shapes(tree, value):
    base, overlay = tree
    (base / "ja.md").write_text(document(value))
    assert load_rules(base, overlay).languages["ja"].detectors[0]["detector"]["kind"] == value["kind"]
    for broken in (dict(value, unknown="secret"), {k: v for k, v in value.items() if k != next(k for k in value if k != "kind")}, dict(value, **{next(k for k in value if k != "kind"): None})):
        (base / "ja.md").write_text(document(broken))
        with pytest.raises(ConfigError, match=r"^ERROR: invalid_rules at rules\.$"):
            load_rules(base, overlay)
@pytest.mark.parametrize("old,new", [("revision: 1", "revision: true"), ("language: ja", "language: en"), ("native_reviewed: true", "native_reviewed: true\nextra: secret"), ("## Syntax", "## Unknown"), ("## Syntax", "### Syntax"), ('<a id="ja-vocabulary-001"></a>', ""), ('"schema_version": 1', '"schema_version": true'), ('"rules":', '"extra": 1, "rules":'), ('"description":', '"description": "duplicate", "description":'), ('"section": "vocabulary"', '"section": "syntax"'), ('"value": "bad"', '"value": ""')])
def test_ctr03_document_rejections(tree, old, new):
    base, overlay = tree
    (base / "ja.md").write_text(document().replace(old, new))
    with pytest.raises(ConfigError):
        load_rules(base, overlay)
def test_ctr03_overlay_versions_and_immutability(tree):
    base, overlay = tree
    first = load_rules(base, overlay)
    manifest = [[p.name if p.name == "common.md" else "base/" + p.name, hashlib.sha256(p.read_bytes()).hexdigest()] for p in base.iterdir()]
    assert first.rules_version == "sha256:" + hashlib.sha256(json.dumps(sorted(manifest), separators=(",", ":")).encode()).hexdigest()
    assert first.common_version == "sha256:" + hashlib.sha256(b"Common\r\n").hexdigest()
    assert load_rules(base, overlay, ("Config",)).rules_version == first.rules_version
    overlay.mkdir()
    (overlay / "ja.md").write_text(document(number="002", term="Overlay", empty=True))
    combined = load_rules(base, overlay, ("Config",))
    assert combined.languages["ja"].protected_terms == ("Base", "Config", "Overlay") and len(combined.languages["ja"].detectors) == 2
    assert combined.rules_version != first.rules_version and combined.languages["ja"].prose.index("001") < combined.languages["ja"].prose.index("002")
    with pytest.raises(TypeError):
        combined.languages["ja"].detectors[0]["detector"]["value"] = "changed"
    (base / "ja.md").write_text(document().replace("Change awkward", "Change verbose"))
    assert load_rules(base, overlay).rules_version != combined.rules_version
    (overlay / "ja.md").write_text(document())
    with pytest.raises(ConfigError):
        load_rules(base, overlay)
@pytest.mark.parametrize("failure", ["unknown", "symlink", "missing_common", "invalid_utf8", "unreadable", "unknown_overlay", "empty_base"])
def test_ctr04_rule_paths_fail_closed(tree, monkeypatch, failure):
    base, overlay = tree
    if failure == "unknown": (base / "unknown.txt").write_text("secret")
    if failure == "symlink": (base / "en.md").symlink_to(base / "ja.md")
    if failure == "missing_common": (base / "common.md").unlink()
    if failure == "invalid_utf8": (base / "ja.md").write_bytes(b"\xff")
    if failure == "unreadable": monkeypatch.setattr(Path, "read_bytes", lambda *a: (_ for _ in ()).throw(PermissionError("secret")))
    if failure == "empty_base": (base / "ja.md").unlink()
    if failure == "unknown_overlay":
        overlay.mkdir()
        (overlay / "en.md").write_text(document())
    with pytest.raises(ConfigError, match=r"^ERROR: invalid_rules at rules\.$"):
        load_rules(base, overlay)
ASSETS = Path(__file__).resolve().parents[2] / "rules"
if any(p.name not in ("common.md", "README.md") for p in ASSETS.iterdir()):
    @pytest.mark.consumer("CTR-03")
    def test_ctr03_installed_assets(tmp_path):
        assert load_rules(ASSETS, tmp_path / "absent").languages
