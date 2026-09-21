"""Dependency gate; the production language resolver is intentionally not connected."""
import json
import subprocess
import sys
from importlib.metadata import distribution


# Run cold and isolate peak RSS from the rest of the suite.
PROBE = r'''
import json, math, resource, socket, sys, time

def denied(*args, **kwargs):
    raise AssertionError("Language detection must not use the network")
socket.socket = denied
socket.create_connection = denied
start = time.monotonic()
from lingua import Language, LanguageDetectorBuilder
builder = LanguageDetectorBuilder.from_all_languages()
detector = builder.with_preloaded_language_models().build()
startup = time.monotonic() - start
cases = [
    ("これは日本語の文章です。", "ja"),
    ("This is a short English sentence.", "en"),
    ("这是一段中文句子。", "zh"),
    ("Das ist ein deutscher Satz.", "de"),
]
for text, expected in cases:
    values = detector.compute_language_confidence_values(text)
    assert len(values) == 75
    assert values[0].language.iso_code_639_1.name.lower() == expected
    assert values[0].value - values[1].value >= 0.20
    assert detector.detect_language_of(text) == values[0].language
    # Zero-confidence ties have no ordering guarantee; compare by ISO identifier.
    snapshot = {v.language.iso_code_639_1.name: v.value for v in values}
    for _ in range(3):
        repeated = detector.compute_language_confidence_values(text)
        assert repeated[0].language == values[0].language
        assert repeated[0].value - repeated[1].value >= 0.20
        assert all(math.isclose(v.value, snapshot[v.language.iso_code_639_1.name],
                                rel_tol=1e-12, abs_tol=1e-15) for v in repeated)
for text in ("", "12345", "?!"):
    assert detector.detect_language_of(text) is None
    assert all(v.value == 0 for v in detector.compute_language_confidence_values(text))
# 12,000 body characters plus at most 31 separators between 32 items.
body = ("This is an English sentence about a local library. " * 300)[:12031]
start = time.monotonic()
assert detector.detect_language_of(body) == Language.ENGLISH
maximum = time.monotonic() - start
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
rss_mib = rss / (1024 ** 2 if sys.platform == "darwin" else 1024)
print(json.dumps(dict(startup_s=startup, maximum_body_s=maximum, peak_rss_mib=rss_mib)))
assert startup < 30 and maximum < 5 and rss_mib < 512
'''


def test_pinned_wheel_includes_license_and_models():
    package = distribution("lingua-language-detector")
    assert package.version == "2.2.0"
    license_path = next(p for p in package.files if str(p).endswith("licenses/LICENSE"))
    assert "Apache License" in package.locate_file(license_path).read_text()
    assert package.metadata["Requires-Python"] == ">=3.12"


def test_all_languages_are_repeatable_offline_within_resource_envelope():
    completed = subprocess.run([sys.executable, "-c", PROBE], capture_output=True,
                               text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    metrics = json.loads(completed.stdout)
    print(metrics)
