"""US-08 control evidence only; owner live/native/client acceptance stays pending."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import JUDGMENT_MODULES, Phase1Contracts, judgment_fingerprint, judgment_inventory
from tests.integration.test_phase1 import collected_contracts, run, suite

ROOT = Path(__file__).resolve().parents[2]


def test_ac_08_1_2_3_4_5_6_7_8_9_10_11_12_15_16_17_18_ctr01_ctr04_fixed_consumers(collected_contracts):
    assert len(JUDGMENT_MODULES) == 23
    assert 'tests/unit/test_judgment_v2_batch.py' in JUDGMENT_MODULES
    assert all(judgment_fingerprint(ROOT, module, collected_contracts.items) == expected
               for module, expected in JUDGMENT_MODULES.items())
    assert len(judgment_inventory()) == len(JUDGMENT_MODULES)


@pytest.mark.parametrize('module', sorted(JUDGMENT_MODULES))
@pytest.mark.parametrize('change', ['module', 'case', 'replacement'])
def test_ac_08_1_7_12_16_ctr01_ctr04_missing_or_replaced_real_consumer_fails(collected_contracts, module, change):
    items = list(collected_contracts.items)
    selected = next(i for i in items if i.nodeid.split('::')[0] == module)
    if change == 'module': items = [i for i in items if i.nodeid.split('::')[0] != module]
    elif change == 'case': items.remove(selected)
    else:
        items[items.index(selected)] = SimpleNamespace(nodeid=selected.nodeid, callspec=SimpleNamespace(params={'replaced': True}))
    assert judgment_fingerprint(ROOT, module, items) != JUDGMENT_MODULES[module]
    if change != 'replacement':
        gate = Phase1Contracts(collected_contracts.config)
        gate.pytest_collection_finish(SimpleNamespace(items=items))
        assert f'Inventory mismatch: {module}::*' in gate.errors


@pytest.mark.parametrize('change', ['ok', 'delete', 'case', 'replace', 'parameter', 'body', 'skip', 'xfail', 'xpass'])
def test_ac_08_1_5_7_9_12_ctr01_ctr04_v4_gate_through_cli_without_keys(suite, monkeypatch, change):
    for key in ('TYPESAFE_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    module = 'tests/test_judgment.py'
    path = suite / module
    source = 'import pytest\n@pytest.mark.parametrize("case", ["one", "two"])\ndef test_cases(case):\n    assert case in ("one", "two")\n'
    path.write_text(source)
    items = [SimpleNamespace(nodeid=module + '::test_cases[' + value + ']',
                             callspec=SimpleNamespace(params={'case': value})) for value in ('one', 'two')]
    expected = judgment_fingerprint(suite, module, items)
    with (suite / 'conftest.py').open('a') as file:
        file.write('\nJUDGMENT_MODULES = ' + repr({module: expected}) + '\n'
                   '_legacy_inventory = phase1_inventory\n'
                   'def phase1_inventory(root):\n    return {**_legacy_inventory(root), **judgment_inventory()}\n')
    if change == 'delete': path.unlink()
    elif change == 'case': path.write_text(source.replace('["one", "two"]', '["one"]'))
    elif change == 'replace': path.write_text(source.replace('test_cases', 'test_other'))
    elif change == 'parameter': path.write_text(source.replace('["one", "two"]', '["wrong", "two"], ids=["one", "two"]'))
    elif change == 'body': path.write_text(source.replace('assert case in ("one", "two")', 'pass'))
    elif change in ('skip', 'xfail'): path.write_text(source.replace('assert case in ("one", "two")', f'pytest.{change}()'))
    elif change == 'xpass': path.write_text(source.replace('def test_cases', '@pytest.mark.xfail\ndef test_cases'))
    result = run(suite, 'tests')
    assert result.returncode == (0 if change == 'ok' else 1), result.stdout + result.stderr
    assert ('CONTRACTS PASS' if change == 'ok' else 'CONTRACTS FAIL') in result.stdout
    assert 'US-08 live/native/client acceptance not evaluated' in result.stdout
    assert 'ACCEPTANCE PASS' not in result.stdout
    if change in ('skip', 'xfail', 'xpass'): assert 'Incomplete or unsuccessful execution' in result.stdout


@pytest.mark.parametrize('outcome', ['passed', 'skipped', 'xfail', 'xpass', 'missing'])
def test_ac_08_7_9_12_ctr01_ctr04_execution_gate_is_independent_of_source_pins(collected_contracts, outcome):
    item = next(i for i in collected_contracts.items if i.nodeid.startswith('tests/unit/test_judgment_v2_batch.py::'))
    gate = Phase1Contracts(collected_contracts.config)
    gate.items = [item]
    for when in ('setup', 'call', 'teardown'):
        if outcome == 'missing' and when == 'call': continue
        report = SimpleNamespace(nodeid=item.nodeid, when=when, passed=outcome != 'skipped' or when != 'call')
        if when == 'call' and outcome in ('xfail', 'xpass'): report.wasxfail = 'expected failure'
        gate.pytest_runtest_logreport(report)
    session = SimpleNamespace(exitstatus=0)
    gate.pytest_sessionfinish(session, 0)
    assert session.exitstatus == (0 if outcome == 'passed' else pytest.ExitCode.TESTS_FAILED)
