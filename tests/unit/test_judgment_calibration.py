"""Complete offline evidence derives candidates without authorizing production use."""
import json
import subprocess
import sys

import pytest

from tests.unit.test_judgment_evaluation import evaluation


@pytest.mark.parametrize('operation', ['calibrate', 'verify', 'verify-report'])
def test_incomplete_generation_four_calibration_refuses_before_provider_calls(tmp_path, operation):
    plan, artifact = tmp_path / 'plan.json', tmp_path / 'artifact.json'
    plan.write_text(evaluation.encoded(evaluation.freeze(1)))
    historical = json.dumps(dict(schema_version=3, quality_accepted=True, source='historical'))
    artifact.write_text(historical)
    result = subprocess.run([sys.executable, 'scripts/judgment_evaluation.py', operation,
        '--plan', str(plan), '--artifact', str(artifact)], capture_output=True, text=True)
    assert result.returncode == 2 and 'complete population and owner manifest' in result.stderr
    assert artifact.read_text() == historical and 'QUALITY PASS' not in result.stdout


@pytest.mark.parametrize('population, error', [('calibration-v3', 'Invalid comparison plan'), ('judgment-acceptance-v3', 'Invalid comparison plan')])
def test_old_population_cannot_freeze_current_calibration_or_acceptance(population, error):
    with pytest.raises(ValueError, match=error):
        evaluation.freeze(1, name=population)


def test_old_pair_manifest_cannot_supply_current_owner_labels():
    with pytest.raises(ValueError, match='not complete'):
        evaluation.freeze(1, pairs={'pairs': [{'owner_label': 'pass'}]})


@pytest.fixture(scope='module')
def ledger():
    import asyncio
    from unittest.mock import patch
    from copyeditor.providers.typesafe import TypeSafe
    cases = evaluation.population('calibration')
    natural = {c['bad'] for c in cases if not c['must_change']}
    original = TypeSafe.evaluate
    async def evaluate(self, wire):
        state = json.loads(wire)['state']
        result = await original(self, wire)
        if 'originals' not in state:
            result = result._replace(blocks=tuple(b._replace(probabilities=(('gate',
                .2 if state['texts'][f'b{b.ordinal:04}']['text'] in natural else .9),)) for b in result.blocks))
        return result
    plan = evaluation.freeze(1, name='calibration')
    with patch.object(evaluation.legacy, 'save', lambda *args: None), patch.object(TypeSafe, 'evaluate', evaluate):
        artifact = asyncio.run(evaluation.run(plan, None))
    pairs, trials = [], []
    for case in cases:
        for kind in sorted(evaluation.PAIR_KINDS):
            identity = case['id'] + '/' + kind
            pairs.append(dict(id=identity, source_id=case['id'], kind=kind,
                candidate=case['bad'] if kind == 'same' else kind + ': ' + case['good'],
                accepted=kind == 'improved', reason='Synthetic prior judgment, not owner evidence.'))
            candidate = .9 if kind == 'same' else .85 if kind == 'unimproved' else .2
            meaning = .9 if kind == 'improved' else 1 if kind in ('same', 'unimproved') else .1
            trials.extend(dict(pair_id=identity, repeat=repeat, source_gate=.9,
                candidate_gate=candidate, meaning=meaning, error=None) for repeat in range(1, 6))
    pair_plan = dict(source_manifest_hash=evaluation.legacy.digest(evaluation.encoded(plan).encode()),
        owner='fixture-reviewer', labels={c['id']: dict(kind='problem' if c['must_change'] else 'natural',
            reason='Synthetic source label.') for c in cases}, pairs=pairs, search_limit=10000)
    pair_artifact = dict(trials=trials)
    seal_pairs(pair_plan, pair_artifact)
    return plan, artifact, pair_plan, pair_artifact


def seal_pairs(plan, artifact):
    artifact.update(manifest_bytes=evaluation.encoded(plan),
                    manifest_hash=evaluation.legacy.digest(evaluation.encoded(plan).encode()))


def test_ac_08_13_14_calibration_derives_three_exact_candidates_without_registration(ledger):
    from copyeditor import judgment_v2
    result = evaluation.calibrate(*ledger)
    assert (result['N'], result['U'], result['G']) == ('0.2', '0.9', '0.7')
    assert result['thresholds'] == dict(floor='0.55', gap='0.7', meaning_floor='0.9')
    assert result['accepted_improvement_trials'] == 150 and result['status'] == 'candidate'
    assert not result['quality_accepted'] and not result['production_registered']
    assert not judgment_v2.THRESHOLDS and not judgment_v2.COMPATIBLE_PAIRS


@pytest.mark.parametrize('change', ['owner', 'labels', 'reason', 'pair_label', 'control', 'pair_manifest',
    'source_manifest', 'malformed_labels', 'malformed_pairs', 'pair_id', 'missing_error', 'missing', 'duplicate', 'repeat', 'failure', 'missing_value', 'bool', 'nan', 'range', 'source_failure'])
def test_ac_08_13_14_incomplete_or_unjudged_calibration_cannot_produce_candidates(ledger, change):
    from copy import deepcopy
    plan, artifact, pairs, measured = deepcopy(ledger)
    if change == 'owner': pairs['owner'] = ''
    elif change == 'labels': pairs['labels'].pop(next(iter(pairs['labels'])))
    elif change == 'reason': pairs['pairs'][0]['reason'] = ''
    elif change == 'pair_label': pairs['pairs'][0]['accepted'] = None
    elif change == 'control': pairs['pairs'].pop()
    elif change == 'pair_manifest': measured['manifest_hash'] = 'replaced'
    elif change == 'source_manifest': pairs['source_manifest_hash'] = 'replaced'
    elif change == 'malformed_labels': pairs['labels'] = []
    elif change == 'malformed_pairs': pairs['pairs'] = [None]
    elif change == 'pair_id': pairs['pairs'][0]['id'] = ''
    elif change == 'missing_error': measured['trials'][0].pop('error')
    elif change == 'missing': measured['trials'].pop()
    elif change == 'duplicate': measured['trials'].append(deepcopy(measured['trials'][0]))
    elif change == 'repeat': measured['trials'][0]['repeat'] = True
    elif change == 'failure': measured['trials'][0]['error'] = 'provider_error'
    elif change == 'missing_value': measured['trials'][0].pop('meaning')
    elif change in ('bool', 'nan', 'range'): measured['trials'][0]['meaning'] = {'bool': True, 'nan': float('nan'), 'range': 1.01}[change]
    else: artifact['trials'][0]['error_code'] = 'provider_error'
    if change != 'pair_manifest': seal_pairs(pairs, measured)
    with pytest.raises((ValueError, KeyError)):
        evaluation.calibrate(plan, artifact, pairs, measured)


def replace_detection(artifact, natural, problem):
    for trial in artifact['trials']:
        source_gate = natural if int(trial['example_id'].rsplit('-', 1)[1]) > 20 else problem
        for event in trial['trace']:
            if event['kind'] == 'judgment' and event['phase'] == 'detect':
                for block in event['result']['blocks']:
                    # Grouped input IDs follow the frozen 01..30 order in this fixture.
                    start = (int(trial['example_id'].rsplit('-', 1)[1]) - 1) // 5 * 5
                    number = start + block['ordinal'] if trial['layout'] == 'items' else int(trial['example_id'].rsplit('-', 1)[1])
                    block['probabilities']['gate'] = natural if number > 20 else problem
            elif event['kind'] == 'verification':
                event['source_gate'] = source_gate


@pytest.mark.parametrize('natural, problem', [(.9, .9), (.95, .9)])
def test_ac_08_13_14_nonpositive_separation_records_n_u_g_without_fallback(ledger, natural, problem):
    from copy import deepcopy
    plan, artifact, pairs, measured = deepcopy(ledger)
    replace_detection(artifact, natural, problem)
    result = evaluation.calibrate(plan, artifact, pairs, measured)
    assert result['status'] == 'not_separated' and result['thresholds'] is None
    assert result['N'] == str(natural) and result['U'] == str(problem)
    assert result['G'] == ('0.0' if natural == problem else '-0.05')


def test_ac_08_14_inclusive_decimal_boundary_and_selection_ties(ledger):
    from copy import deepcopy
    from decimal import localcontext
    plan, artifact, pairs, measured = deepcopy(ledger)
    for trial in measured['trials']:
        if trial['pair_id'].endswith('/improved'):
            trial.update(source_gate=.3, candidate_gate=.2)
    with localcontext() as context:
        context.prec = 2
        result = evaluation.calibrate(plan, artifact, pairs, measured)
    assert result['thresholds'] == dict(floor='0.55', gap='0.1', meaning_floor='0.9')
    assert result['accepted_improvement_trials'] == 150


def test_ac_08_14_acceptance_count_precedes_stricter_gap_and_meaning(ledger):
    from copy import deepcopy
    plan, artifact, pairs, measured = deepcopy(ledger)
    for trial in measured['trials']:
        if trial['pair_id'].endswith('/improved') and trial['repeat'] == 1:
            trial.update(candidate_gate=.1, meaning=.7)
    result = evaluation.calibrate(plan, artifact, pairs, measured)
    assert result['accepted_improvement_trials'] == 150
    assert result['thresholds'] == dict(floor='0.55', gap='0.7', meaning_floor='0.7')


@pytest.mark.parametrize('change', ['unsafe_control', 'zero_improvements', 'budget'])
def test_ac_08_14_no_feasible_or_exhausted_search_never_registers_defaults(ledger, change):
    from copy import deepcopy
    plan, artifact, pairs, measured = deepcopy(ledger)
    if change == 'budget':
        pairs['search_limit'] = 1
        seal_pairs(pairs, measured)
    else:
        for trial in measured['trials']:
            if change == 'unsafe_control' and trial['pair_id'].endswith('/meaning'):
                trial.update(candidate_gate=.1, meaning=1)
            elif change == 'zero_improvements' and trial['pair_id'].endswith('/improved'):
                trial['candidate_gate'] = 1
    result = evaluation.calibrate(plan, artifact, pairs, measured)
    assert result['status'] == ('search_budget_exceeded' if change == 'budget' else 'no_feasible_thresholds')
    assert result['thresholds'] is None and not result['production_registered']


def test_ac_08_14_calibration_cli_saves_candidate_report_without_quality_acceptance(tmp_path, ledger):
    plan, artifact, pair_plan, pair_artifact = ledger
    paths = {name: tmp_path / (name + '.json') for name in ('plan', 'artifact', 'pairs')}
    for name, value in [('plan', plan), ('artifact', artifact), ('pairs', dict(manifest=pair_plan, observations=pair_artifact))]:
        paths[name].write_text(evaluation.encoded(value))
    result = subprocess.run([sys.executable, 'scripts/judgment_evaluation.py', 'calibrate',
        '--plan', str(paths['plan']), '--artifact', str(paths['artifact']), '--pairs', str(paths['pairs'])], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == 'candidate', result.stderr
    report = json.loads(paths['artifact'].read_text())['calibration']
    assert report['thresholds'] == dict(floor='0.55', gap='0.7', meaning_floor='0.9')
    assert not report['quality_accepted'] and not report['production_registered']


def test_ac_08_13_floor_keeps_midpoint_between_adjacent_float_observations(ledger):
    from copy import deepcopy
    plan, artifact, pairs, measured = deepcopy(ledger)
    replace_detection(artifact, .5, .5000000000000001)
    result = evaluation.calibrate(plan, artifact, pairs, measured)
    assert result['G'] == '0.0000000000000001'
    assert result['thresholds']['floor'] == '0.50000000000000005'


@pytest.mark.parametrize('meaning', [0, 1])
def test_ac_08_14_domain_endpoints_remain_inclusive_candidates(ledger, meaning):
    from copy import deepcopy
    plan, artifact, pairs, measured = deepcopy(ledger)
    for trial in measured['trials']:
        if trial['pair_id'].endswith('/improved'):
            trial.update(source_gate=1, candidate_gate=0, meaning=meaning)
    result = evaluation.calibrate(plan, artifact, pairs, measured)
    assert result['thresholds'] == dict(floor='0.55', gap='1', meaning_floor=str(meaning))
    assert result['accepted_improvement_trials'] == 150
