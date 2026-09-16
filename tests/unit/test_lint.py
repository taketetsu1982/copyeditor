import pytest
from copyeditor.config import ConfigError
from copyeditor.lint import LintError, compile_rule, lint, lint_response
from copyeditor.rules import LanguageRules
def language(d):
    return LanguageRules("", (), (compile_rule(dict(id="en-vocabulary-001", description="Message", detector=d)),))
@pytest.mark.parametrize("d,bad,good,spans", [
    ({"kind": "literal", "value": "é"}, "😀éé", "e", [(1, 2), (2, 3)]),
    ({"kind": "regex", "pattern": "é*"}, "😀éé", "e", [(1, 3)]),
    ({"kind": "sentence_length", "max": 3, "terminators": "."}, "  abcd.\r\n短。", "ab.", [(2, 7)]),
    ({"kind": "comma_count", "max": 1, "commas": ",", "terminators": "."}, "a,b,c.", "a,b.", [(0, 6)]),
    ({"kind": "repeated_ending", "endings": ["end", "longend"], "min_run": 2, "terminators": "."}, "end. end. longend. longend.", "end. other.", [(0, 9), (10, 27)]),
    ({"kind": "brackets", "pairs": ["()", "[]"]}, "([)]", "([])", [(0, 1), (2, 3)]),
    ({"kind": "width_mix", "half": "a", "full": "ａ"}, " aａ\nx", "a\nａ", [(1, 3)]),
])
def test_ctr03_all_detectors(d, bad, good, spans):
    rules = language(d)
    assert [(f.start, f.end) for f in lint(bad, rules)] == spans
    assert lint(good, rules) == () and lint(bad, rules) == lint(bad, rules)
@pytest.mark.parametrize("size", [159, 160, 161])
@pytest.mark.parametrize("count", [99, 100, 101])
def test_ctr01_caps_and_uncapped(size, count):
    rules = language({"kind": "literal", "value": "x" * size})
    text = ("x" * size + " ") * count
    rules = rules._replace(detectors=rules.detectors * 2)
    full, output = lint(text, rules), lint_response(text, rules)
    assert len(full) == count and len(output["findings"]) == min(100, count) and output["findings_truncated"] == (count > 100)
    assert full[0].matched == "x" * min(160, size) and full[0].end == size and full[0].matched_truncated == (size > 160)
@pytest.mark.parametrize("pattern", ["(?U)a", "(?=a)", "(a)\\1", "["])
def test_ctr03_compile_failure_is_fixed(pattern, capsys):
    with pytest.raises(ConfigError, match="invalid_rules at rules"):
        language({"kind": "regex", "pattern": pattern})
    assert capsys.readouterr() == ("", "")
def test_ctr03_runtime_failure_is_not_empty(monkeypatch):
    from copyeditor import lint as module
    monkeypatch.setattr(module, "spans", lambda *args: (_ for _ in ()).throw(ValueError("secret")))
    with pytest.raises(LintError, match="^internal_error$"):
        lint("x", language({"kind": "literal", "value": "x"}))
