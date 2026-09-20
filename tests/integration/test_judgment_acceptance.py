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
from tests.unit.test_judgment_calibration import evaluation, ledger, verification
from tests.unit.test_judgment_evaluation import reviewed

ROOT = Path(__file__).resolve().parents[2]


def test_ac_08_13_14_fixed_evaluation_modules_and_populations(collected_contracts):
    assert set(EVALUATION_MODULES) == {
        'tests/unit/test_judgment_examples.py', 'tests/unit/test_judgment_evaluation.py',
        'tests/unit/test_judgment_calibration.py', 'tests/integration/test_judgment_acceptance.py'}
    assert {k: v[0] for k, v in EVALUATION_SETS.items()} == {
        'judgment-calibration': 15, 'judgment-acceptance': 20, 'rewrite': 24}
    assert len(evaluation_inventory(ROOT)) == 4
    for module, expected in EVALUATION_MODULES.items():
        assert judgment_fingerprint(ROOT, module, collected_contracts.items) == expected
    for name, size in [('calibration', 600), ('acceptance', 400), ('existing', 480), ('regression', 200)]:
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


@pytest.mark.parametrize('missing', ['gate', 'verify', 'neither'])
def test_ac_08_13_14_gate_and_verify_completion_are_independent(verification, missing):
    plan, artifact = deepcopy(verification[:2])
    if missing == 'gate': next(t for t in artifact['trials'] if t['judgment_enabled'])['calibration']['gate_probability'] = None
    if missing == 'verify': artifact['verification_trials'][0]['probabilities'] = None
    gate = evaluation.calibrate(plan, artifact)
    verify = evaluation.verification_summary(plan, artifact)
    assert gate['calibration_summary']['complete'] is (missing != 'gate')
    assert verify['complete'] is (missing != 'verify')
    assert (gate['calibration_decision']['derived_floor'] is None) is (missing == 'gate')
    assert (verify['fail_max'] is None and verify['pass_min'] is None) is (missing == 'verify')
    if missing == 'neither':
        summary, decision = gate['calibration_summary'], gate['calibration_decision']
        assert (summary['natural_max'], summary['unnatural_min'], summary['gap'], decision['derived_floor']) == ('0.6', '0.8', '0.2', '0.7')
        assert summary['per_case_variation'] and summary['by_degree_layout_repeat']
        assert decision['edit_risk']['recommendation'] == 'keep_disabled_false_veto'
        assert verify['per_pair_variation'] and verify['confusion'] and verify['items']
        assert decision['reason'] == 'new_threshold_id_contract_hash_and_remeasurement_required'
        assert verify['reason'] == 'new_threshold_id_contract_hash_and_recalibration_before_unused_held_out'
    assert not verify['quality_accepted'] and set(US08_OWNER_EVIDENCE) == {'live', 'native', 'client'}


def test_ac_08_13_14_synthetic_reviews_and_native_or_client_claims_cannot_accept_live_quality(reviewed):
    plan, artifact, _ = deepcopy(reviewed)
    assert evaluation.summarize(plan, artifact)['criteria_met']
    artifact.update(native='approved', client='approved', live='approved', quality_accepted=True)
    assert not evaluation.summarize(plan, artifact)['quality_accepted']
    for row in artifact['trials']: row.update(reviewer=None, decision=dict.fromkeys('abcd'), reasons=dict.fromkeys('abcd'))
    result = evaluation.summarize(plan, artifact)
    assert not result['complete'] and not result['criteria_met'] and not result['quality_accepted']


@pytest.mark.parametrize('field', ['questions', 'action_instructions', 'references', 'floor', 'verification'])
def test_ac_08_13_14_changed_policy_or_threshold_cannot_reuse_frozen_calibration(verification, monkeypatch, field):
    plan = verification[0]
    thresholds = field in ('floor', 'verification')
    registry = evaluation.THRESHOLDS if thresholds else evaluation.POLICIES
    identity = plan['config']['judgment.thresholds_version' if thresholds else 'judgment.policy_version']
    changed = json.loads(evaluation.encoded(registry[identity]))
    if field == 'floor': changed[field] = .54
    elif field == 'verification': changed[field]['pass_min'] = .71
    elif field == 'questions': changed[field]['verify'][0]['instructions'] += ' Changed wording.'
    elif field == 'references': changed[field][0]['text'] += ' Changed reference.'
    else: changed[field]['simplify_vocabulary'] += ' Changed action vocabulary or instruction.'
    monkeypatch.setattr(evaluation, 'THRESHOLDS' if thresholds else 'POLICIES', {**registry, identity: changed})
    with pytest.raises(ValueError, match='Comparison inputs changed'): evaluation.check_plan(plan)


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
