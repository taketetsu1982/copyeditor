"""Current-runner guarantees; fixture success never supplies owner acceptance."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import judgment_evaluation as evaluation


@pytest.fixture(scope='module')
def completed(tmp_path_factory):
    path = tmp_path_factory.mktemp('current-evaluation') / 'artifact.json'
    plan = evaluation.freeze(1, name='existing')
    with patch.object(evaluation.legacy, 'save', lambda *args: None):
        artifact = asyncio.run(evaluation.run(plan, path))
    evaluation.legacy.save(path, artifact)
    return plan, artifact, path


def test_existing_population_keeps_all_trials_and_current_responses(completed):
    plan, artifact, path = completed
    assert len(plan['sets'][0]['cases']) == 24
    assert len(artifact['trials']) == len(evaluation.audit(plan, artifact)) == 480
    assert json.loads(path.read_text()) == artifact
    for trial in artifact['trials']:
        payload = trial['full_response']
        assert payload['status'] == 'ok' and payload['schema_version'] == 4
        assert payload['judgment_enabled'] is trial['judgment_enabled']
        assert 'verification' not in payload and trial['error_code'] is None
        assert trial['decision'] == dict.fromkeys('abcd') and trial['reviewer'] is None
    result = evaluation.summarize(plan, artifact)
    assert result['status'] == 'unreviewed_or_invalid' and not result['quality_accepted']
    assert not result['criteria_met']


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'moved', 'manifest', 'condition', 'unexecuted', 'unmarked', 'measurements', 'policy_hash'])
def test_missing_or_fabricated_trial_evidence_is_rejected(completed, mutation):
    plan, artifact, _ = deepcopy(completed)
    trial = artifact['trials'][0]
    if mutation == 'missing': artifact['trials'].pop()
    elif mutation == 'duplicate': artifact['trials'].append(deepcopy(trial))
    elif mutation == 'moved': trial['example_id'] = 'other'
    elif mutation == 'manifest': artifact['manifest_hash'] = 'replaced'
    elif mutation == 'condition': trial['judgment_enabled'] = not trial['judgment_enabled']
    elif mutation == 'measurements': trial['provider_measurements'] = []
    elif mutation == 'policy_hash': plan['conditions'][1]['policy_hash'] = 'changed'
    elif mutation == 'unexecuted': trial['started_at'] = None
    else: trial.update(full_response=None, error_code=None)
    with pytest.raises(ValueError): evaluation.audit(plan, artifact)


@pytest.mark.parametrize('mutation', ['version', 'rules', 'model', 'unknown_usage', 'diagnosis'])
def test_current_schema_and_correlations_reject_changed_results(completed, mutation):
    plan, artifact, _ = deepcopy(completed)
    trial = next(t for t in artifact['trials'] if t['degree'] == 'rewrite')
    payload = trial['full_response']
    if mutation == 'version': payload['schema_version'] = 2
    elif mutation == 'rules': payload['rules_version'] = 'sha256:' + '0' * 64
    elif mutation == 'model': payload['providers'][0]['model'] = 'other'
    elif mutation == 'unknown_usage': payload['providers'][0]['usage']['total_tokens'] = True
    else: payload['diagnosis'] = None
    trial['provider_measurements'] = deepcopy(payload['providers'])
    result = evaluation.summarize(plan, artifact)
    assert any(group['counts']['integrity'] for group in result['groups'].values())
    assert not result['criteria_met'] and not result['quality_accepted']


@pytest.mark.parametrize('name, error', [('calibration-v3', 'Invalid comparison plan'), ('judgment-acceptance-v3', 'Invalid comparison plan')])
def test_old_populations_are_not_new_calibration_or_acceptance(name, error):
    with pytest.raises(ValueError, match=error):
        evaluation.freeze(1, name=name)


@pytest.mark.asyncio
async def test_failures_remain_in_the_full_planned_denominator(tmp_path):
    plan = evaluation.freeze(1, name='existing')
    async def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE')
    with patch.object(evaluation.legacy, 'save', lambda *args: None):
        artifact = await evaluation.run(plan, tmp_path / 'failed.json', fail)
    assert len(evaluation.audit(plan, artifact)) == 480
    assert all(t['full_response'] is None and t['error_code'] == 'evaluation_error' for t in artifact['trials'])
    assert 'PRIVATE' not in evaluation.encoded(artifact)
    assert not evaluation.summarize(plan, artifact)['criteria_met']


@pytest.mark.asyncio
async def test_comparison_sends_no_owner_labels_or_tone_to_judgment(monkeypatch):
    from copyeditor.providers.typesafe import TypeSafe
    wires, original = [], TypeSafe.evaluate
    async def inspect(self, wire, **kwargs):
        wires.append(json.loads(wire))
        return await original(self, wire, **kwargs)
    monkeypatch.setattr(TypeSafe, 'evaluate', inspect)
    case = dict(evaluation.population('existing')[0], reason='PRIVATE_OWNER_LABEL', reviewer='PRIVATE_OWNER_LABEL')
    case['background'] = dict(case['background'], tone='PRIVATE_TONE')
    plans = []
    response = await evaluation.adapter.compare_request([case], 'rewrite', True, 'text', 'fixture', plans)
    assert response['status'] == 'ok' and wires
    assert 'PRIVATE_' not in repr(wires) and case['good'] not in json.dumps(wires[0]['state'], ensure_ascii=False)
    assert all('desired_style' not in w['state'] and 'tone' not in w['state']['background'] for w in wires)
    assert [p['candidate_round'] for p in plans] == [0, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize('degree', ['polish', 'rewrite'])
async def test_second_candidate_round_keeps_both_verifications_and_usage(monkeypatch, degree):
    from copyeditor.providers.typesafe import TypeSafe
    from copyeditor.providers.base import Usage
    original, seen = TypeSafe.evaluate, []
    async def retry(self, wire, **kwargs):
        result = await original(self, wire, **kwargs)
        seen.append(wire)
        if len(seen) == 2:
            result = result._replace(blocks=tuple(b._replace(probabilities=tuple(
                (key, .9) for key, value in b.probabilities)) for b in result.blocks))
        return result._replace(usage=Usage(2, 3, 5))
    monkeypatch.setattr(TypeSafe, 'evaluate', retry)
    plans = []
    result = await evaluation.adapter.compare_request([evaluation.population('existing')[0]], degree,
        True, 'text', 'fixture', plans)
    assert result['status'] == 'ok' and [p['candidate_round'] for p in plans] == [0, 1, 2]
    assert result['providers'][0]['model_calls'] == 2 and result['providers'][1]['model_calls'] == 3
    assert result['providers'][1]['usage'] == dict(input_tokens=6, output_tokens=9, total_tokens=15)


@pytest.mark.asyncio
async def test_rewrite_runner_retains_independent_population_and_unreviewed_status(tmp_path):
    runner = evaluation.legacy
    plan = runner.freeze(1)
    with patch.object(runner, 'save', lambda *args: None):
        artifact = await runner.run(plan, tmp_path / 'rewrite.json')
    assert len([t for t in artifact['trials'] if t['group'] == 'acceptance']) == 120
    assert all(t['response']['schema_version'] == 4 and t['error'] is None for t in artifact['trials'])
    result = runner.summarize(plan, artifact)
    assert result['status'] == 'unreviewed' and not result['criteria_met'] and not result['quality_accepted']
    cases = runner.fixtures()
    for trial in artifact['trials']:
        required = 'abc' if cases[trial['id']]['must_change'] else 'bcd'
        trial['judgment'].update({key: True for key in required}, reason='Synthetic positive control only.')
    assert runner.summarize(plan, artifact)['criteria_met']
    trial = artifact['trials'][0]
    trial['response'] = await evaluation.adapter.compare_request([cases[trial['id']]], 'polish', False,
        'text', 'fixture', [])
    assert trial['response']['status'] == 'ok' and trial['response']['degree'] == 'polish'
    result = runner.summarize(plan, artifact)
    assert result['must_failures'] == 1 and not result['criteria_met'] and not result['quality_accepted']
