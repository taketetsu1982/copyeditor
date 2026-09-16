from collections import Counter
from decimal import Decimal
import pytest
from copyeditor.preservation import check, matched_terms, number_tokens, occurrences, request_terms, url_tokens, variable_tokens
from copyeditor.providers.base import SourceItem

RATIO = {"min": 0.5, "max": 2}

@pytest.mark.parametrize("original,candidate,terms,failed", [
    ("Product words", "Changed words", ["Product"], ("protected_terms",)),
    ("Value 10 units", "Value 20 units", [], ("numbers",)),
    ("Visit https://a.test", "Visit https://b.test", [], ("urls",)),
    ("Hello ${name}", "Hello ${user}", [], ("variables",)),
    ("abcd", "a", [], ("length_ratio",)),
    ("Product 10 https://a.test ${name}", "changed", ["Product"], ("protected_terms", "numbers", "urls", "variables", "length_ratio")),
    ("Keep Product 10 ${name} https://a.test", "Keep Product 10 ${name} https://a.test", ["Product"], ()),
    ("10 20", "20 10", [], ()),
    ("ordinary wording", "Product wording", ["Product"], ()),
])
def test_ctr02_checks_and_failure_order(original, candidate, terms, failed):
    result = check(original, candidate, terms, RATIO)
    assert result.failed == failed and result.html_valid is None
    assert result == check(original, candidate, terms, RATIO, "markdown")

@pytest.mark.parametrize("text,expected", [
    ("+10 -２０ −٣.٤％ ＋５ －６", ["+10", "-２０", "−٣.٤％", "＋５", "－６"]),
    ("1,000 1000 １．２ 3/4 5:6 7-8 ９，０ １／２ ３：４ ５－６", ["1,000", "1000", "１．２", "3/4", "5:6", "7-8", "９，０", "１／２", "３：４", "５－６"]),
    ("1. x 2, - 3 +4% ² four", ["1", "2", "3", "+4%"]),
    ("1−2 1--2 10 10", ["1", "−2", "1", "-2", "10", "10"]),
])
def test_ctr02_unicode_number_tokens(text, expected):
    assert number_tokens(text) == Counter(expected)

@pytest.mark.parametrize("suffix", list(".,;:!?)]}。、！？）」』") + [".)！？", "\u00a0", "\u2028", "\u3000", "<", ">", '"', "'", "`"])
def test_ctr02_url_boundaries(suffix):
    assert url_tokens("https://a.test" + suffix + " ") == Counter({"https://a.test": 1})

def test_ctr02_url_exactness_and_non_whitespace_controls():
    assert url_tokens("HTTP://x ftp://x /relative http:// http://a http://a") == Counter({"http://a": 2})
    assert url_tokens("https://a\x1cb") == Counter({"https://a\x1cb": 1})

@pytest.mark.parametrize("text,expected", [
    ("${name} {{name}} {name} %s %d %f %(name)s %(name)d %(name)f", ["${name}", "{{name}}", "{name}", "%s", "%d", "%f", "%(name)s", "%(name)d", "%(name)f"]),
    ("%%s %%d %%%f %%%%s", ["%f"]),
    ("${a.b-c_9} {{_name}} {2bad} {é} %x %(a)x", ["${a.b-c_9}", "{{_name}}"]),
    ("{{x}} {{x}} {x}", ["{{x}}", "{{x}}", "{x}"]),
])
def test_ctr02_longest_variable_forms(text, expected):
    assert variable_tokens(text) == Counter(expected)

@pytest.mark.parametrize("original,candidate,kind", [("10 10", "10 xx", "numbers"), ("x x", "1 x", "numbers"), ("https://a xxxxxxxxx", "https://a https://a", "urls"), ("${x} ${x}", "${x} xxxx", "variables"), ("xxxxxxxx", "${x}xxxx", "variables")])
def test_ctr02_token_multisets_detect_additions_and_removals(original, candidate, kind):
    assert check(original, candidate, (), RATIO).failed == (kind,)

def test_ac_02_10_overlaps_and_independent_shorter_terms():
    assert occurrences("aaaa", "aa") == 3
    assert matched_terms("aaaa Product", ["aa", "a", "aa", "Product", "absent"]) == ("Product", "a", "aa")
    assert check("aaaa", "aaa", ["a", "aa"], RATIO).failed == ("protected_terms",)
    assert check("Café", "Café", ["Café"], RATIO).failed == ("protected_terms",)

def test_ac_02_11_request_count_excludes_context_and_retry_multiplicity():
    items = (SourceItem("a", "Product alpha", "ContextOnly"), SourceItem("b", "Product beta", "Unused"))
    assert request_terms(items, ["Product", "Product", "alpha", "ContextOnly", "Absent"]) == ("Product", "alpha")
    assert len(request_terms(items * 2, ["Product", "alpha", "ContextOnly"])) == 2

@pytest.mark.parametrize("length,failed", [(1, True), (2, False), (8, False), (9, True)])
def test_ctr02_inclusive_ratio(length, failed):
    assert ("length_ratio" in check("abcd", "x" * length, (), RATIO).failed) == failed

def test_ctr02_ratio_has_no_rounding_or_whitespace_normalization():
    assert check("😀 \r\n", "xx", (), RATIO).failed == ()
    ratio = {"min": Decimal("0.500000000000000000000000000000001"), "max": 2}
    assert check("abcd", "xx", (), ratio).failed == ("length_ratio",)
    assert check("abcd", "x", (), {"min": Decimal("1e-1000000000"), "max": 2}).failed == ()
    with pytest.raises(ValueError):
        check("", "x", (), RATIO)
    with pytest.raises(ValueError, match="separate consumer"):
        check("<p>x</p>", "<p>x</p>", (), RATIO, "html")
