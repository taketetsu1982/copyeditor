"""AC-08-13/14 control evidence; fixture results never certify owner acceptance."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from tests.conftest import (EVALUATION_MODULES, EVALUATION_SETS, US08_OWNER_EVIDENCE,
                           Phase1Contracts, evaluation_inventory, judgment_fingerprint)
from tests.integration.test_phase1 import collected_contracts, run, suite
from tests.unit.test_judgment_calibration import evaluation
from tests.unit.test_judgment_evaluation import reviewed

ROOT = Path(__file__).resolve().parents[2]


def test_ac_08_13_14_fixed_evaluation_modules_and_populations(collected_contracts):
    assert set(EVALUATION_MODULES) == {
        'tests/unit/test_judgment_examples.py', 'tests/unit/test_judgment_evaluation.py',
        'tests/unit/test_judgment_calibration.py', 'tests/integration/test_judgment_acceptance.py',
        'tests/unit/test_generation4_evaluation.py', 'tests/unit/test_judgment_v2_examples.py'}
    assert {k: v[0] for k, v in EVALUATION_SETS.items()} == {
        'judgment-calibration': 30, 'judgment-acceptance': 40, 'rewrite': 24, 'judgment-v2-calibration': 30}
    assert len(evaluation_inventory(ROOT)) == 6
    for module, expected in EVALUATION_MODULES.items():
        assert judgment_fingerprint(ROOT, module, collected_contracts.items) == expected
    for name, size in [('existing', 480), ('regression', 200), ('calibration', 1200)]:
        plan = evaluation.freeze(1, name=name)
        assert len(plan['planned_trials']) == size
        assert {t['degree'] for t in plan['planned_trials']} == {'polish', 'rewrite'}
        assert {t['repeat'] for t in plan['planned_trials']} == set(range(1, 6))


@pytest.mark.parametrize('module', sorted(EVALUATION_MODULES))
@pytest.mark.parametrize('change', ['module', 'case', 'replacement'])
def test_ac_08_13_14_missing_evaluation_consumer_fails(collected_contracts, module, change):
    items = list(collected_contracts.items)
    selected = next(i for i in items if i.nodeid.split('::')[0] == module)
    if change == 'module': items = [i for i in items if i.nodeid.split('::')[0] != module]
    elif change == 'case': items.remove(selected)
    else: items[items.index(selected)] = SimpleNamespace(nodeid=selected.nodeid, iter_markers=selected.iter_markers, callspec=SimpleNamespace(id='replacement', params={'replacement': True}))
    gate = Phase1Contracts(collected_contracts.config)
    gate.pytest_collection_finish(SimpleNamespace(items=items))
    assert f'Inventory mismatch: {module}::*' in gate.errors


@pytest.mark.parametrize('prefix', sorted(EVALUATION_SETS))
@pytest.mark.parametrize('change', ['delete', 'replace', 'add'])
def test_ac_08_13_14_missing_or_changed_population_fails(tmp_path, prefix, change):
    shutil.copytree(ROOT / 'examples', tmp_path / 'examples')
    path = tmp_path / f'examples/ja/{prefix}-01.yaml'
    if change == 'delete': path.unlink()
    elif change == 'replace': path.write_text(path.read_text() + '\n# Changed after approval\n')
    else: path.with_name(prefix + '-99.yaml').write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match='evaluation population'): evaluation_inventory(tmp_path)


@pytest.mark.parametrize('outcome', ['ok', 'delete', 'skip', 'xfail', 'xpass'])
def test_ac_08_13_14_strict_green_without_owner_records_is_not_quality_pass(suite, monkeypatch, outcome):
    for key in ('TYPESAFE_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_API_KEY', 'COPYEDITOR_ACCEPTANCE_EVIDENCE', 'COPYEDITOR_OWNER'):
        monkeypatch.delenv(key, raising=False)
    module = 'tests/test_evaluation.py'; path = suite / module
    source = 'import pytest\ndef test_evaluation():\n    assert True\n'
    path.write_text(source)
    expected = judgment_fingerprint(suite, module, [SimpleNamespace(nodeid=module + '::test_evaluation')])
    with (suite / 'conftest.py').open('a') as file:
        file.write('\n_prior_inventory = phase1_inventory\ndef phase1_inventory(root):\n    return {**_prior_inventory(root), ' + repr(module + '::*') + ': ' + repr({expected[1]: {'count': expected[0]}}) + '}\n')
    if outcome == 'delete': path.unlink()
    elif outcome in ('skip', 'xfail'): path.write_text(source.replace('assert True', f'pytest.{outcome}()'))
    elif outcome == 'xpass': path.write_text(source.replace('def test_evaluation', '@pytest.mark.xfail\ndef test_evaluation'))
    result = run(suite, 'tests')
    assert result.returncode == (0 if outcome == 'ok' else 1), result.stdout + result.stderr
    assert ('CONTRACTS PASS' if outcome == 'ok' else 'CONTRACTS FAIL') in result.stdout
    assert all(f'US-08 {kind}: NOT EVALUATED' in result.stdout for kind in ('live', 'native', 'client'))
    assert 'QUALITY PASS' not in result.stdout and 'ACCEPTANCE PASS' not in result.stdout




def test_ac_08_13_14_synthetic_reviews_and_native_or_client_claims_cannot_accept_live_quality(reviewed):
    plan, artifact, _ = deepcopy(reviewed)
    assert evaluation.summarize(plan, artifact)['criteria_met']
    artifact.update(native='approved', client='approved', live='approved', quality_accepted=True)
    assert not evaluation.summarize(plan, artifact)['quality_accepted']
    for row in artifact['trials']: row.update(reviewer=None, decision=dict.fromkeys('abcd'), reasons=dict.fromkeys('abcd'))
    result = evaluation.summarize(plan, artifact)
    assert not result['complete'] and not result['criteria_met'] and not result['quality_accepted']




@pytest.mark.parametrize('module', sorted(EVALUATION_MODULES))
@pytest.mark.parametrize('outcome', ['passed', 'skipped', 'xfail', 'xpass', 'missing'])
def test_ac_08_13_14_evaluation_execution_cannot_be_replaced_by_collection(collected_contracts, module, outcome):
    item = next(i for i in collected_contracts.items if i.nodeid.split('::')[0] == module)
    gate = Phase1Contracts(collected_contracts.config); gate.items = [item]
    for when in ('setup', 'call', 'teardown'):
        if outcome == 'missing' and when == 'call': continue
        report = SimpleNamespace(nodeid=item.nodeid, when=when, passed=outcome != 'skipped' or when != 'call')
        if when == 'call' and outcome in ('xfail', 'xpass'): report.wasxfail = 'Fixture only'
        gate.pytest_runtest_logreport(report)
    session = SimpleNamespace(exitstatus=0)
    gate.pytest_sessionfinish(session, 0)
    assert session.exitstatus == (0 if outcome == 'passed' else pytest.ExitCode.TESTS_FAILED)
