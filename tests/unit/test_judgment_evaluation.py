import asyncio
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import judgment_evaluation as evaluation


@pytest.fixture(scope='module')
def completed(tmp_path_factory):
    path = tmp_path_factory.mktemp('judgment') / 'artifact.json'
    plan = evaluation.freeze(1)
    artifact = asyncio.run(evaluation.run(plan, path))
    assert json.loads(path.read_text()) == artifact
    return plan, artifact, path


def test_ac_08_13_14_all_trials_requests_and_packing_are_retained(completed):
    plan, artifact, path = completed
    requests = evaluation.audit(plan, artifact)
    assert len(artifact['trials']) == 600 and len(requests) == 360
    assert all(t['full_response']['status'] == 'ok' and not t['error_code'] for t in artifact['trials'])
    assert all(len(t['batch_plans']) == (2 if t['judgment_enabled'] else 0) for t in artifact['trials'])
    assert all(t['decision'] == dict.fromkeys('abcd') and t['reviewer'] is None for t in artifact['trials'])
    assert all((t['calibration'] is not None) == t['judgment_enabled'] for t in artifact['trials'])
    assert sum(r['provider_measurements'][0]['model_calls'] for r in requests.values()) < sum(t['provider_measurements'][0]['model_calls'] for t in artifact['trials'])
    for name, blocks, calls in [('acceptance', 400, 400), ('existing', 480, 480), ('regression', 200, 40)]:
        frozen = evaluation.freeze(1, name=name)
        assert len(frozen['planned_trials']) == blocks and len(frozen['request_layouts']) == calls
    frozen_path = path.with_name('plan.json'); evaluation.legacy.save(frozen_path, plan)
    command = [sys.executable, 'scripts/judgment_evaluation.py']
    result = subprocess.run(command + ['check', '--plan', str(frozen_path), '--artifact', str(path)], capture_output=True, text=True)
    assert result.returncode == 0 and '360 request observations; quality unreviewed' in result.stdout
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
    assert len(evaluation.audit(plan, artifact)) == 360
    assert all(t['error_code'] == ('evaluation_error' if t['layout'] == 'text' else 'provider_error') for t in artifact['trials'])
    assert all(t['full_response'] is None for t in artifact['trials'] if t['layout'] == 'text')
    assert 'PRIVATE' not in evaluation.encoded(artifact)


@pytest.mark.asyncio
async def test_ac_08_13_14_interruption_saves_observed_plan_and_rejects_unrun_trials(tmp_path):
    async def cancel(cases, degree, enabled, layout, mode, plans):
        plans.append(dict(phase='detect', batches=[])); raise asyncio.CancelledError()
    plan, path = evaluation.freeze(1), tmp_path / 'interrupted.json'
    with pytest.raises(asyncio.CancelledError): await evaluation.run(plan, path, cancel)
    artifact = json.loads(path.read_text())
    assert artifact['trials'][0]['batch_plans'] == [dict(phase='detect', batches=[])]
    with pytest.raises(ValueError): evaluation.audit(plan, artifact)
