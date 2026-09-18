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

sys.path.insert(0, str(ROOT / "scripts"))
import benchmark_provider as adapter
from benchmark_assert import get_assert
from copyeditor import preservation
from copyeditor.providers.base import ProviderFailure, Usage

CASES = converter["load_examples"](ROOT / "examples", load_rules(ROOT / "rules", None))


def example():
    return dict(CASES[0], bad="Product has 10 items at https://example.com/a for {name}.",
                good="Product offers 10 items at https://example.com/a for {name}.",
                protected_terms=["Product"], lint=None, must_change=True)


@pytest.mark.consumer("CTR-03")
@pytest.mark.consumer("CTR-05")
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["language"] + "/" + c["id"])
@pytest.mark.asyncio
async def test_ac04_3_ac04_4_ac04_5_ctr03_ctr05_real_examples_accepted(case, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Fixture must not construct Vertex")
    monkeypatch.setattr("copyeditor.providers.vertex.Vertex", forbidden)
    response = await adapter.call_api(case["bad"], {}, {"vars": case})
    result = json.loads(response["output"])
    assert result["text"] == case["good"] and result["flag"] is None and result["model_calls"] == (2 if case.get("degree") == "rewrite" else 1)
    assert get_assert(response["output"], {"vars": case})


@pytest.mark.parametrize("name,old,new", [("protected_terms", "Product", "Other"), ("numbers", "10", "11"),
    ("urls", "example.com/a", "example.com/b"), ("variables", "{name}", "{other}"), ("length_ratio", "", "x" * 200)])
@pytest.mark.asyncio
async def test_ac04_3_ctr05_mutations_fail_named_checks(name, old, new):
    case = example()
    candidate = case["good"].replace(old, new) if old else case["good"] + new
    config, snapshot = adapter.environment(case, "fixture")
    checked = preservation.check(case["bad"], candidate, snapshot.languages["en"].protected_terms, {"min": .5, "max": 2})
    assert name in checked.failed
    response = await adapter.call_api("", {}, {"vars": case})
    output = json.loads(response["output"])
    output["text"] = candidate
    assert not get_assert(json.dumps(output), {"vars": case})
    response = await adapter.call_api("", {}, {"vars": case | {"good": candidate}})
    assert json.loads(response["output"])["flag"]["kind"] == "rejected"


@pytest.mark.asyncio
async def test_ac04_3_ctr05_html_and_final_schema():
    case = example() | {"format": "html", "bad": "<p>Product has 10 items.</p>", "good": "<p>Product offers 10 items.</p>"}
    response = await adapter.call_api("", {}, {"vars": case})
    assert get_assert(response["output"], {"vars": case})
    output = json.loads(response["output"])
    for changed in (output | {"text": output["text"].replace("<p>", "<div>").replace("</p>", "</div>")},
                    output | {"extra": True}, output | {"flag": {"kind": "unfixable", "reason": "Cannot edit", "checks": []}}):
        assert not get_assert(json.dumps(changed), {"vars": case})
    response = await adapter.call_api("", {}, {"vars": case | {"good": "<div>Product offers 10 items.</div>"}})
    assert json.loads(response["output"])["error"]["code"] == "html_structure"


@pytest.mark.asyncio
async def test_ac04_4_ctr05_fixture_network_attempt_raises(monkeypatch):
    import socket
    async def access(self, request):
        socket.create_connection(("example.com", 443))
    monkeypatch.setattr(adapter.FixtureProvider, "generate", access)
    with pytest.raises(RuntimeError, match="Fixture network access denied"):
        await adapter.call_api("", {}, {"vars": example()})


@pytest.mark.asyncio
async def test_ac04_4_ctr05_live_input_isolation_and_no_fallback(monkeypatch, tmp_path):
    case = example() | {"reason": "SECRET_REASON", "lint": None, "background": {"audience": "readers"}}
    config, snapshot = adapter.environment(case, "fixture")
    monkeypatch.setattr(adapter, "environment", lambda c, m: (config, snapshot))
    seen = []
    class Live:
        def __init__(self, supplied):
            assert supplied is config
        async def generate(self, request):
            seen.append(request)
            return await adapter.FixtureProvider(case["good"] + " Today.").generate(request)
    monkeypatch.setattr("copyeditor.providers.vertex.Vertex", Live)
    response = await adapter.call_api("SECRET_PROMPT", {"config": {"mode": "live"}}, {"vars": case})
    assert seen[0].items[0].text == case["bad"] and seen[0].background.audience == "readers"
    assert seen[0].language == "en" and seen[0].format == "text"
    assert all(value not in repr(seen[0]) for value in (case["good"], case["reason"], "SECRET_PROMPT"))
    import benchmark_assert
    monkeypatch.setattr(benchmark_assert, "environment", lambda c, m: (config, snapshot))
    assert get_assert(response["output"], {"vars": case, "config": {"mode": "live"}})
    assert not get_assert(response["output"], {"vars": case})
    async def fail(self, request):
        return ProviderFailure("provider_error", Usage(None, None, None))
    monkeypatch.setattr(Live, "generate", fail)
    response = await adapter.call_api("", {"config": {"mode": "live"}}, {"vars": case})
    assert json.loads(response["output"])["status"] == "error"
    assert not get_assert(response["output"], {"vars": case, "config": {"mode": "live"}})
    converter["convert"](tmp_path / "live.json", [case], live=True)
    converted = json.loads((tmp_path / "live.json").read_text())
    assert converted["providers"][0]["config"]["mode"] == converted["tests"][0]["assert"][0]["config"]["mode"] == "live"


@pytest.mark.asyncio
async def test_ac04_3_ctr05_uncapped_lint_and_must_change(monkeypatch):
    import benchmark_assert
    case = example() | {"bad": "noise " * 101 + "in order to proceed", "good": "noise " * 101 + "to proceed",
                        "lint": {"rule_ids": ["en-vocabulary-001"]}}
    config, snapshot = adapter.environment(case, "fixture")
    rules = snapshot.languages["en"]
    noise = dict(id="en-vocabulary-002", description="Noise", detector=dict(kind="literal", value="noise"))
    snapshot = snapshot._replace(languages=dict(snapshot.languages, en=rules._replace(detectors=rules.detectors + (noise,))))
    monkeypatch.setattr(adapter, "environment", lambda c, m: (config, snapshot))
    monkeypatch.setattr(benchmark_assert, "environment", lambda c, m: (config, snapshot))
    response = await adapter.call_api("", {}, {"vars": case})
    assert json.loads(response["output"])["findings_truncated"]
    assert get_assert(response["output"], {"vars": case})
    lingering = case | {"good": case["bad"] + "."}
    response = await adapter.call_api("", {}, {"vars": lingering})
    assert not get_assert(response["output"], {"vars": lingering})
    unchanged = example() | {"good": example()["bad"]}
    response = await adapter.call_api("", {}, {"vars": unchanged})
    assert not get_assert(response["output"], {"vars": unchanged})
    assert get_assert(response["output"], {"vars": unchanged | {"must_change": False}})


def rewrite_example(change=True):
    case = example()
    return case | dict(degree="rewrite", good=case["good"] if change else case["bad"], must_change=change,
        rewrite_expectations=dict(problems=["Awkward expression."] if change else [], invariants=["Keep facts and register."]))


@pytest.mark.parametrize("change", [dict(degree=None), dict(degree=True), dict(degree="other"),
    dict(rewrite_expectations=None), dict(rewrite_expectations={}),
    dict(rewrite_expectations=dict(problems=[], invariants=["Fact"])),
    dict(rewrite_expectations=dict(problems=["Problem"], invariants=[])),
    dict(rewrite_expectations=dict(problems=["Problem"], invariants=["Fact", "Fact"])),
    dict(rewrite_expectations=dict(problems=[" "], invariants=["Fact"])),
    dict(rewrite_expectations=dict(problems=["x" * 321], invariants=["Fact"])),
    dict(rewrite_expectations=dict(problems=[str(i) for i in range(9)], invariants=["Fact"])),
    dict(rewrite_expectations=dict(problems=["Problem"], invariants=["Fact"], extra=True)),
    dict(degree="polish"), dict(must_change=False)])
def test_ac_07_8_ctr05_rewrite_schema_is_closed_and_bounded(assets, change):
    base, examples = assets
    path = examples / "ja/vocabulary.yaml"
    case = json.loads(path.read_text()) | dict(degree="rewrite",
        rewrite_expectations=dict(problems=["Problem"], invariants=["Fact"]))
    path.write_text(json.dumps(case | change))
    with pytest.raises(ValueError, match="^Invalid examples$"):
        converter["load_examples"](examples, load_rules(base, None))


@pytest.mark.parametrize("change", [True, False])
def test_ac_07_8_ctr05_rewrite_vars_preserve_expectations_and_nonchange(assets, tmp_path, change):
    base, examples = assets
    path = examples / "ja/vocabulary.yaml"
    case = json.loads(path.read_text()) | dict(degree="rewrite", must_change=change, lint=None)
    if not change: case["good"] = case["bad"]
    case["rewrite_expectations"] = dict(problems=["p" * 320] if change else [], invariants=["f" * 320])
    path.write_text(json.dumps(case))
    cases = converter["load_examples"](examples, load_rules(base, None))
    selected = next(c for c in cases if c["id"] == "vocabulary")
    assert selected["rewrite_expectations"] == case["rewrite_expectations"]
    assert all(c["degree"] == "polish" and "rewrite_expectations" not in c for c in cases if c is not selected)
    output = tmp_path / "rewrite.json"
    converter["convert"](output, cases)
    assert [test["vars"] for test in json.loads(output.read_text())["tests"]] == cases


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [True, False])
async def test_ac_07_8_ac_07_13_ctr05_fixture_uses_real_diagnosis_and_candidate_flow(change, monkeypatch):
    case = rewrite_example(change)
    seen = []
    original = adapter.FixtureProvider.generate
    async def record(self, data):
        seen.append(data)
        return await original(self, data)
    monkeypatch.setattr(adapter.FixtureProvider, "generate", record)
    response = await adapter.call_api("IGNORED_PROMPT", {}, {"vars": case})
    result = json.loads(response["output"])
    assert result["schema_version"] == 2 and result["degree"] == "rewrite" and result["model_calls"] == 2
    assert result["text"] == case["good"] and result["diagnosis"]["status"] == ("issue" if change else "no_issue")
    assert [value.stage for value in seen] == ["diagnose", "rewrite"]
    assert get_assert(response["output"], {"vars": case})
    assert not get_assert(response["output"], {"vars": case | {"degree": "polish"}})


@pytest.mark.asyncio
async def test_ac_07_9_ctr05_rewrite_live_input_never_contains_evaluation_metadata(monkeypatch):
    case = rewrite_example() | {"reason": "EVALUATOR_REASON", "rewrite_expectations": {
        "problems": ["EVALUATOR_PROBLEMS"], "invariants": ["EVALUATOR_INVARIANTS"]}}
    config, snapshot = adapter.environment(case, "fixture")
    monkeypatch.setattr(adapter, "environment", lambda c, m: (config, snapshot))
    import benchmark_assert
    monkeypatch.setattr(benchmark_assert, "environment", lambda c, m: (config, snapshot))
    seen, counts = [], []
    class Live:
        def __init__(self, supplied): assert supplied is config
        async def estimate_input(self, data):
            counts.append(data)
            return 0
        async def generate(self, data):
            seen.append(data)
            return await adapter.FixtureProvider(case["good"]).generate(data)
    monkeypatch.setattr("copyeditor.providers.vertex.Vertex", Live)
    response = await adapter.call_api("EVALUATOR_PROMPT", {"config": {"mode": "live"}}, {"vars": case})
    assert [value.stage for value in seen] == ["diagnose", "rewrite"] and counts == seen
    assert all("EVALUATOR" not in repr(value) and case["good"] not in repr(value) for value in seen)
    assert all(value.items[0].text == case["bad"] for value in seen)
    assert get_assert(response["output"], {"vars": case, "config": {"mode": "live"}})
    # Nonchange applies to actual live output, independently of valid server diagnostics.
    natural = case | dict(must_change=False, good=case["bad"])
    assert not get_assert(response["output"], {"vars": natural, "config": {"mode": "live"}})
    async def fail(self, data): return ProviderFailure("provider_error", Usage(None, None, None))
    monkeypatch.setattr(Live, "estimate_input", fail)
    seen.clear()
    response = await adapter.call_api("", {"config": {"mode": "live"}}, {"vars": case})
    assert json.loads(response["output"])["error"]["code"] == "provider_error" and not seen


@pytest.mark.asyncio
async def test_ac_07_9_ctr05_rewrite_fixture_denies_network_during_preflight(monkeypatch):
    import socket
    async def access(self, data): socket.create_connection(("example.com", 443))
    monkeypatch.setattr(adapter.FixtureProvider, "estimate_input", access)
    with pytest.raises(RuntimeError, match="Fixture network access denied"):
        await adapter.call_api("", {}, {"vars": rewrite_example()})


@pytest.mark.asyncio
async def test_ctr05_array_metadata_is_data_not_an_evaluation_matrix(tmp_path):
    case = example() | dict(bad="Level Alpha has 10 items.", good="Level Alpha offers 10 items.",
                            protected_terms=["Level", "Alpha"])
    output = tmp_path / "arrays.json"
    converter["convert"](output, [case])
    tests = json.loads(output.read_text())["tests"]
    assert len(tests) == 1 and tests[0]["vars"] == case
    # Promptfoo otherwise expands string arrays into separate scalar-valued trials.
    assert tests[0]["options"]["disableVarExpansion"] is True
    response = await adapter.call_api("", {}, {"vars": tests[0]["vars"]})
    result = json.loads(response["output"])
    assert result["protected_terms"] == ["Alpha", "Level"] and result["protected_terms_checked"] == 2
    assert get_assert(response["output"], {"vars": case})
    for scalar in ("Level", None, ("Level", "Alpha")):
        with pytest.raises(ValueError, match="^Invalid benchmark protected terms$"):
            await adapter.call_api("", {}, {"vars": case | dict(protected_terms=scalar)})
