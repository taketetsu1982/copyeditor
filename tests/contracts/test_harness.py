import subprocess
import sys
from pathlib import Path

import pytest

from .harness import MAX_STRING, ProviderQueue, assert_bytes_equal, assert_subset, expand, load_cases

ROOT = Path(__file__).resolve().parents[2]


def test_nested_expansion_preserves_ordinary_data():
    assert expand({"items": [{"$concat": ["a", {"$repeat": ["b", 2]}]}, 7]}) == {"items": ["abb", 7]}
    assert expand({"$repeat": ["x", MAX_STRING]}) == "x" * MAX_STRING
    assert expand({"$repeat": ["", 10**100]}) == ""


@pytest.mark.parametrize("value", [
    {"$repeat": ["x", -1]}, {"$repeat": ["x", True]}, {"$repeat": [3, 2]},
    {"$repeat": ["x"]}, {"$repeat": ["x", 1], "extra": 0},
    {"$repeat": ["x", MAX_STRING + 1]}, {"$concat": "x"}, {"$concat": [1]},
    {"$concat": [{"$repeat": ["x", MAX_STRING]}, "x"]},
])
def test_invalid_expansion_is_rejected(value):
    with pytest.raises(ValueError):
        expand(value)


@pytest.mark.parametrize("file,kind", [("contracts/tools.md", "contract-case"), ("rules/common.md", "html-case")])
def test_document_fixtures_are_loaded(file, kind):
    cases = load_cases(ROOT / file, kind)
    assert len(cases) == (ROOT / file).read_text().count(f"```json {kind}\n") > 0


@pytest.mark.parametrize("actual,expected", [({}, {"a": 1}), ([1], []), (True, 1), (2, 1)])
def test_partial_comparison_rejects_mismatches(actual, expected):
    with pytest.raises(AssertionError):
        assert_subset(actual, expected)


def test_comparisons_and_queue_detect_drift():
    assert_subset({"a": [{"b": 1, "extra": 2}]}, {"a": [{"b": 1}]})
    assert_bytes_equal(b"exact\r\n", b"exact\r\n")
    with pytest.raises(AssertionError):
        assert_bytes_equal(b"exact\r\n", b"exact\n")
    queue = ProviderQueue([{"id": "a"}, {"id": "b"}])
    with pytest.raises(AssertionError):
        queue.assert_exhausted()
    assert [queue.take(), queue.take()] == [{"id": "a"}, {"id": "b"}]
    queue.assert_exhausted()
    with pytest.raises(AssertionError):
        queue.take()


def test_collection_reports_unconnected_consumers():
    result = subprocess.run([sys.executable, "-m", "pytest", __file__, "--collect-only", "-q"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(f"UNCONNECTED CTR-{n:02}" in result.stdout for n in range(1, 6))
