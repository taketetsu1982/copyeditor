import asyncio
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import judgment_evaluation as evaluation


@pytest.fixture(scope='module')
def completed(tmp_path_factory):
    path = tmp_path_factory.mktemp('judgment') / 'artifact.json'
    plan = evaluation.freeze(1)
    # Ledger assertions need every trial, but checkpoint I/O is covered separately.
    with patch.object(evaluation.legacy, 'save', lambda *args: None):
        artifact = asyncio.run(evaluation.run(plan, path))
    evaluation.legacy.save(path, artifact)
    assert json.loads(path.read_text()) == artifact
    return plan, artifact, path


def test_ac_08_13_14_all_trials_requests_and_packing_are_retained(completed):
    plan, artifact, path = completed
    requests = evaluation.audit(plan, artifact)
    assert len(artifact['trials']) == 1200 and len(requests) == 720
    assert plan['calibration_run_budget'] == dict(blocks=1200, requests=720)
    assert plan['risk_probe']['max_calls'] == 600 and plan['risk_probe']['input_budget'] == 38400000
    assert all(t['full_response']['status'] == 'ok' and not t['error_code'] for t in artifact['trials'])
    assert all(len(t['batch_plans']) == (2 if t['judgment_enabled'] else 0) for t in artifact['trials'])
    assert all(t['decision'] == dict.fromkeys('abcd') and t['reviewer'] is None for t in artifact['trials'])
    assert all((t['calibration'] is not None) == t['judgment_enabled'] for t in artifact['trials'])
    assert sum(r['provider_measurements'][0]['model_calls'] for r in requests.values()) < sum(t['provider_measurements'][0]['model_calls'] for t in artifact['trials'])
    for name, blocks, calls in [('acceptance', 800, 800), ('existing', 480, 480), ('regression', 200, 40)]:
        frozen = evaluation.freeze(1, name=name)
        assert len(frozen['planned_trials']) == blocks and len(frozen['request_layouts']) == calls
    frozen_path = path.with_name('plan.json'); evaluation.legacy.save(frozen_path, plan)
    command = [sys.executable, 'scripts/judgment_evaluation.py']
    result = subprocess.run(command + ['check', '--plan', str(frozen_path), '--artifact', str(path)], capture_output=True, text=True)
    assert result.returncode == 0 and '720 request observations; quality unreviewed' in result.stdout
    help_text = subprocess.check_output(command + ['--help'], text=True)
    assert all(option in help_text for option in ('plan,run,check', '--mode', '--set', '--revision', '--artifact'))


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unexpected', 'moved', 'manifest', 'condition', 'boolean', 'plan_boolean', 'unexecuted', 'observation'])
def test_ac_08_13_14_inventory_and_request_observation_mutations_fail(completed, mutation):
    plan, artifact, _ = deepcopy(completed)
    if mutation == 'missing': artifact['trials'].pop()
    elif mutation == 'duplicate': artifact['trials'].append(deepcopy(artifact['trials'][0]))
    elif mutation == 'unexpected': artifact['trials'][0]['example_id'] = 'unexpected'
    elif mutation == 'moved': artifact['trials'][0]['set'] = 'acceptance'
    elif mutation == 'manifest': artifact['manifest_bytes'] += ' '
    elif mutation == 'condition': artifact['trials'][0]['judgment_enabled'] = True
    elif mutation == 'boolean': artifact['trials'][0]['judgment_enabled'] = 0
    elif mutation == 'plan_boolean': plan['planned_trials'][0]['judgment_enabled'] = 0
    elif mutation == 'unexecuted': artifact['trials'][0]['error_code'] = 'not_run'
    else:
        row = next(t for t in artifact['trials'] if t['layout'] == 'items')
        row['latency_ms'] += 1
    with pytest.raises(ValueError): evaluation.audit(plan, artifact)


@pytest.mark.asyncio
async def test_ac_08_13_14_models_receive_no_evaluation_labels(monkeypatch):
    from copyeditor.providers.typesafe import TypeSafe
    wires, edits = [], []
    evaluate = TypeSafe.evaluate
    async def inspect(self, wire, **kw):
        wires.append(json.loads(wire)); return await evaluate(self, wire, **kw)
    async def estimate(self, data): edits.append(data); return 0
    monkeypatch.setattr(TypeSafe, 'evaluate', inspect)
    monkeypatch.setattr(evaluation.adapter.FixtureProvider, 'estimate_input', estimate)
    cases = [dict(c, reason='PRIVATE_EVALUATION_LABEL', kind='PRIVATE_EVALUATION_LABEL', invariants=['PRIVATE_EVALUATION_LABEL']) for c in evaluation.population('calibration')[:5]]
    for degree in ('polish', 'rewrite'):
        for enabled in (False, True):
            assert (await evaluation.adapter.compare_request(cases, degree, enabled, 'items', 'fixture', []))['status'] == 'ok'
    assert edits and wires and 'PRIVATE_EVALUATION_LABEL' not in repr((edits, wires))
    assert 'judgment-calibration' not in repr((edits, wires))
    for wire in wires:
        state = wire['state']; assert set(state) <= {'language', 'background', 'desired_style', 'references', 'texts', 'pairs'}
        if 'references' in state: assert state['references'] == evaluation._json_value(evaluation.REFERENCES)
    plans = []
    await evaluation.adapter.compare_request(evaluation.population('regression'), 'polish', True, 'items', 'fixture', plans)
    assert any(len(p['batches']) > 1 for p in plans)


@pytest.mark.asyncio
async def test_ac_08_13_14_missing_responses_and_atomic_failures_remain_in_ledger(tmp_path):
    async def fail(cases, degree, enabled, layout, mode, plans):
        if layout == 'text': raise RuntimeError('PRIVATE')
        return dict(status='error', error=dict(code='provider_error'), model_calls=1)
    plan = evaluation.freeze(1)
    artifact = await evaluation.run(plan, tmp_path / 'failures.json', fail)
    assert len(evaluation.audit(plan, artifact)) == 720
    assert all(t['error_code'] == ('evaluation_error' if t['layout'] == 'text' else 'provider_error') for t in artifact['trials'])
    assert all(t['full_response'] is None for t in artifact['trials'] if t['layout'] == 'text')
    assert 'PRIVATE' not in evaluation.encoded(artifact)


@pytest.mark.asyncio
async def test_ac_08_13_14_interruption_saves_observed_plan_and_rejects_unrun_trials(tmp_path):
    calls = 0
    async def cancel(cases, degree, enabled, layout, mode, plans):
        nonlocal calls
        calls += 1
        checkpoint = json.loads(path.read_text())
        if calls == 1:
            assert all(t['error_code'] == 'not_run' for t in checkpoint['trials'])
            return dict(status='error', error=dict(code='provider_error'))
        assert checkpoint['trials'][0]['error_code'] == 'provider_error'
        assert checkpoint['trials'][1]['error_code'] == 'not_run'
        plans.append(dict(phase='detect', batches=[])); raise asyncio.CancelledError()
    plan, path = evaluation.freeze(1), tmp_path / 'interrupted.json'
    with pytest.raises(asyncio.CancelledError): await evaluation.run(plan, path, cancel)
    artifact = json.loads(path.read_text())
    assert calls == 2
    assert artifact['trials'][0]['error_code'] == 'provider_error'
    assert artifact['trials'][1]['batch_plans'] == [dict(phase='detect', batches=[])]
    assert artifact['trials'][1]['error_code'] == 'evaluation_error'
    assert artifact['trials'][2]['error_code'] == 'not_run'
    assert not path.with_suffix('.json.tmp').exists()
    with pytest.raises(ValueError): evaluation.audit(plan, artifact)


@pytest.fixture(scope='module', params=['acceptance', 'existing'])
def reviewed(request):
    plan = evaluation.freeze(1, name=request.param)
    cases = {c['id']: c for c in evaluation.population(request.param)}
    async def collect():
        responses = {}
        for degree in ('polish', 'rewrite'):
            for enabled in (False, True):
                for case in cases.values():
                    plans = []
                    response = await evaluation.adapter.compare_request([case], degree, enabled, 'text', 'fixture', plans)
                    responses[degree, enabled, case['id']] = response, plans
        return responses
    responses, trials = asyncio.run(collect()), []
    for row in plan['planned_trials']:
        response, plans = deepcopy(responses[row['degree'], row['judgment_enabled'], row['example_id']])
        problem = cases[row['example_id']]['must_change']
        decision = dict(a=True if problem else None, b=True, c=True, d=None if problem else True)
        trials.append(dict(row, full_response=response, batch_plans=plans, started_at='fixture', error_code=None,
            provider_measurements=response.get('providers'), latency_ms=1, calibration=None, decision=decision,
            reasons={k: 'Synthetic review, not owner evidence.' if v is not None else None for k, v in decision.items()}, reviewer='fixture-reviewer'))
    artifact = dict(manifest_bytes=evaluation.encoded(plan), manifest_hash=evaluation.legacy.digest(evaluation.encoded(plan).encode()), trials=trials)
    off = next(t for t in trials if t['degree'] == 'polish' and not t['judgment_enabled'] and cases[t['example_id']]['must_change'])
    off['full_response']['text'] = cases[off['example_id']]['bad']; off['decision']['a'] = False
    return plan, artifact, cases


def test_ac_08_13_comparison_keeps_population_denominators_and_owner_reasons(reviewed):
    plan, artifact, cases = reviewed
    summary = evaluation.summarize(plan, artifact)
    assert summary['criteria_met'] and summary['complete'] and not summary['quality_accepted']
    assert summary['added_value'] == 1
    for group in summary['groups'].values():
        assert group['counts']['planned'] == len(cases) * 5
        assert group['counts']['problem'] == sum(c['must_change'] for c in cases.values()) * 5
        assert group['counts']['natural'] == sum(not c['must_change'] for c in cases.values()) * 5
        assert all(t['planned'] == 5 for t in group['per_example'].values())
        assert group['requests'] == len(cases) * 5
    assert summary['judgments'][0]['reasons'] == artifact['trials'][0]['reasons']
    assert summary['legacy_criteria_met'] is (True if len(cases) == 24 else None)


@pytest.mark.parametrize('mutation', ['keep', 'missing', 'duplicate', 'condition', 'pending', 'old_version', 'registry', 'old_artifact', 'reason', 'boolean', 'missing_response', 'meaning', 'unnecessary', 'natural'])
def test_ac_08_13_14_unacceptable_comparisons_cannot_pass(reviewed, mutation):
    plan, artifact, cases = deepcopy(reviewed)
    trials = artifact['trials']
    on = next(t for t in trials if t['judgment_enabled'])
    if mutation == 'keep':
        for t in trials:
            if t['judgment_enabled']: t['full_response']['text'] = cases[t['example_id']]['bad']
    elif mutation == 'missing': trials.pop()
    elif mutation == 'duplicate': trials.append(deepcopy(trials[0]))
    elif mutation == 'condition': trials[0]['degree'] = 'rewrite'
    elif mutation == 'pending': on['decision']['b'] = None
    elif mutation == 'reason': on['reasons']['b'] = ' '
    elif mutation == 'boolean': on['decision']['b'] = 1
    elif mutation == 'old_version': on['full_response'] = deepcopy(trials[0]['full_response'])
    elif mutation == 'registry': on['full_response']['thresholds_hash'] = 'sha256:' + '0' * 64
    elif mutation == 'old_artifact': artifact = dict(plan=plan, trials=trials)
    elif mutation == 'missing_response':
        on.update(full_response=None, error_code='evaluation_error'); on['decision'].update(b=None, c=None)
    elif mutation in ('meaning', 'unnecessary'): on['decision']['b' if mutation == 'meaning' else 'c'] = False
    elif mutation == 'natural':
        next(t for t in trials if t['judgment_enabled'] and not cases[t['example_id']]['must_change'])['full_response']['text'] += ' '
    if mutation in ('missing', 'duplicate', 'condition', 'boolean', 'old_artifact'):
        with pytest.raises(ValueError): evaluation.summarize(plan, artifact)
    else:
        summary = evaluation.summarize(plan, artifact)
        assert not summary['criteria_met'] and not summary['quality_accepted']
        assert all(g['counts']['planned'] == len(cases) * 5 for g in summary['groups'].values())
        assert any(j['failures'] for j in summary['judgments'])
        if mutation == 'keep': assert all(g['counts']['improved'] == 0 for k, g in summary['groups'].items() if '/on/' in k)


@pytest.mark.parametrize('scope,failures,passed', [('example', 1, True), ('example', 2, False), ('repeat', 3, True), ('repeat', 4, False)])
def test_ac_08_13_new_and_legacy_rate_boundaries(reviewed, scope, failures, passed):
    plan, artifact, cases = deepcopy(reviewed)
    problem_ids = [i for i, c in cases.items() if c['must_change']]
    if scope == 'repeat' and len(cases) == 40: failures += 3
    for t in artifact['trials']:
        if t['degree'] != 'rewrite' or not t['judgment_enabled']: continue
        if (scope == 'example' and t['example_id'] == problem_ids[0] and t['repeat'] <= failures) or (scope == 'repeat' and t['repeat'] == 1 and t['example_id'] in problem_ids[:failures]): t['decision']['a'] = False
    group = evaluation.summarize(plan, artifact)['groups']['rewrite/on/text']
    assert group['criteria_met'] is passed
    assert group['per_repeat']['1']['planned'] == (30 if len(cases) == 40 else 18)


def test_ac_08_13_equal_quality_is_not_new_value_and_old_off_failure_is_retained(reviewed):
    plan, artifact, cases = deepcopy(reviewed)
    for t in artifact['trials']:
        if t['decision']['a'] is False:
            t['decision']['a'] = True; t['full_response']['text'] = cases[t['example_id']]['good']
    summary = evaluation.summarize(plan, artifact)
    assert summary['added_value'] == 0
    assert summary['criteria_met'] is (len(cases) == 24)
    if len(cases) == 40: assert summary['status'] == 'no_added_value'
    else:
        next(t for t in artifact['trials'] if t['degree'] == 'rewrite' and not t['judgment_enabled'])['decision']['b'] = False
        summary = evaluation.summarize(plan, artifact)
        assert not summary['legacy_criteria_met'] and not summary['criteria_met']
        assert summary['groups']['rewrite/on/text']['criteria_met']


def test_ac_08_13_report_entry_point_retains_reason_and_rejects_unreviewed(completed, tmp_path):
    plan, artifact, _ = deepcopy(completed)
    path, frozen = tmp_path / 'judgment-evaluation.json', tmp_path / 'plan.json'
    evaluation.legacy.save(path, artifact); evaluation.legacy.save(frozen, plan)
    result = subprocess.run([sys.executable, 'scripts/judgment_evaluation.py', 'report', '--plan', str(frozen), '--artifact', str(path)], capture_output=True, text=True)
    assert result.returncode == 1 and 'unreviewed_or_invalid' in result.stdout
    summary = json.loads(path.read_text())['summary']
    assert not summary['criteria_met'] and sum(g['counts']['planned'] for g in summary['groups'].values()) == 1200
    assert 'reviewer' in path.with_suffix('.md').read_text()
    artifact['summary'] = dict(criteria_met=True); artifact['trials'].pop()
    evaluation.legacy.save(path, artifact)
    invalid = subprocess.run(result.args, capture_output=True, text=True)
    assert invalid.returncode == 1 and 'invalid_artifact' in invalid.stdout
    assert not json.loads(path.read_text())['summary']['criteria_met']


def test_ac_08_14_report_costs_count_each_request_once_and_exclude_risk(completed):
    plan, artifact, _ = deepcopy(completed)
    for t in artifact['trials']:
        response = t['full_response']
        response['cost'] = dict(amount='0.010000', currency='USD')
        for provider in response.get('providers', []): provider['cost'] = dict(amount='0.005000', currency='USD')
    summary = evaluation.summarize(plan, artifact)
    for key, group in summary['groups'].items():
        assert group['counts']['planned'] == 150
        assert group['requests'] == (30 if key.endswith('/items') else 150)
        assert group['cost'] == dict(amount='0.300000' if key.endswith('/items') else '1.500000', currency='USD')
    assert summary['status'] == 'unreviewed_or_invalid' and not summary['quality_accepted']


@pytest.mark.parametrize('change', ['criteria', 'model', 'policy', 'source', 'reviewer'])
def test_ac_08_14_frozen_conditions_and_review_identity_cannot_be_reused(reviewed, change):
    plan, artifact, _ = deepcopy(reviewed)
    if change == 'criteria': plan['acceptance_criteria']['comparison']['required_gain'] = 0
    elif change == 'model': plan['conditions'][0]['editing_model'] = 'other-model'
    elif change == 'policy': plan['conditions'][1]['policy_hash'] = '0' * 64
    elif change == 'source': plan['source_commit'] = '0' * 40
    else: artifact['trials'][0]['reviewer'] = None
    if change == 'reviewer':
        summary = evaluation.summarize(plan, artifact)
        assert not summary['criteria_met'] and not summary['complete']
    else:
        with pytest.raises(ValueError): evaluation.summarize(plan, artifact)
