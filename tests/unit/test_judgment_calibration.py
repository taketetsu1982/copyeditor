import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import patch
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import judgment_evaluation as evaluation
from copyeditor.providers.typesafe import TypeSafe


@pytest.fixture(scope='module')
def production(tmp_path_factory):
    plan = evaluation.freeze(1)
    path = tmp_path_factory.mktemp('risk') / 'result.json'
    async def runner(cases, degree, enabled, layout, mode, plans):
        if enabled and layout == 'items' and cases[0]['id'].endswith('01'):
            return await evaluation.adapter.compare_request(cases, degree, enabled, layout, mode, plans)
        return dict(status='error', error=dict(code='provider_error'))
    states, evaluate = [], TypeSafe.evaluate
    async def inspect(self, wire, **kwargs):
        payload = json.loads(wire)
        if 'texts' in payload['state']: states.append(payload['state'])
        return await evaluate(self, wire, **kwargs)
    async def defer(frozen, observed, path):
        assert len(evaluation.audit(frozen, observed)) == 360
    with patch.object(evaluation, 'probe', defer), patch.object(TypeSafe, 'evaluate', inspect):
        artifact = asyncio.run(evaluation.run(plan, path, runner))
    return plan, artifact, states


@pytest.mark.asyncio
async def test_probe_keeps_production_results_and_exact_original_batch_without_labels(production, tmp_path, monkeypatch):
    plan, artifact, states = deepcopy(production)
    for t in artifact['trials']:
        t['reviewer'] = 'PRIVATE_REVIEWER'
        for b in (t['full_response'] or {}).get('items', []):
            b.update(text='PRIVATE_CANDIDATE', verification=dict(status='PRIVATE_VERIFICATION'))
    before = evaluation.audit(plan, artifact)
    wires = []
    original = TypeSafe.evaluate
    async def inspect(self, wire, **kwargs):
        wires.append(json.loads(wire)); return await original(self, wire, **kwargs)
    monkeypatch.setattr(TypeSafe, 'evaluate', inspect)
    with patch('socket.socket', side_effect=AssertionError('Network')), patch('socket.getaddrinfo', side_effect=AssertionError('DNS')):
        await evaluation.probe(plan, artifact, tmp_path / 'risk.json')
    assert evaluation.audit(plan, artifact) == before
    assert len(wires) == len(artifact['risk_measurements']) == 10
    assert sum(m['model_calls'] for m in artifact['risk_measurements'].values()) == 10
    for wire in wires:
        assert wire['state'] in states
        assert len(wire['state']['texts']) == len(wire['questions']) == 5
        assert wire['state']['references'] == evaluation._json_value(evaluation.REFERENCES)
        assert set(wire['state']) == {'texts', 'references', 'language', 'background', 'desired_style'}
        assert all(evaluation.RISK['question'] in q['instructions'] for q in wire['questions'].values())
        assert all('simplify_vocabulary' in q['instructions'] for q in wire['questions'].values())
    text = evaluation.encoded(wires)
    assert 'PRIVATE_' not in text
    assert all(marker not in text for marker in ('judgment-calibration', 'verification', 'fixture-only'))
    for t in artifact['trials']:
        c = t['calibration']
        if c and not t['error_code']:
            assert c['risk_probability'] == .85 and c['risk_error'] is None
            assert artifact['risk_measurements'][c['risk_measurement']]['request_id'] == t['request_id']
        elif c: assert c['risk_not_run_reason'] == 'production_error' and c['risk_probability'] is None
    assert not artifact['risk_complete']
    assert 'fixture-only' not in evaluation.encoded(artifact)
    with pytest.raises(ValueError, match='already attempted'): await evaluation.probe(plan, artifact, tmp_path / 'again.json')


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['exception', 'timeout', 'invalid', 'budget', 'input_budget', 'input', 'ineligible', 'cancel'])
async def test_probe_missing_values_are_explicit_and_do_not_change_production(production, tmp_path, monkeypatch, failure):
    plan, artifact, states = deepcopy(production)
    if failure in ('budget', 'input_budget'):
        monkeypatch.setitem(evaluation.RISK, 'max_calls' if failure == 'budget' else 'input_budget', 1)
        plan = evaluation.freeze(1, created_at=plan['created_at'])
        artifact.update(manifest_bytes=evaluation.encoded(plan), manifest_hash=evaluation.legacy.digest(evaluation.encoded(plan).encode()))
    for t in artifact['trials']:
        if not t['error_code'] and failure == 'input': t['batch_plans'] = []
        if not t['error_code'] and failure == 'ineligible':
            t['full_response']['items'][0]['detection']['status'] = 'insufficient'
    before = deepcopy(evaluation.audit(plan, artifact))
    calls = []
    if failure == 'timeout':
        timeout = asyncio.timeout
        def expire(seconds):
            assert seconds == 10
            return timeout(0)
        monkeypatch.setattr(asyncio, 'timeout', expire)
    async def caller(plan, wire):
        calls.append(wire)
        if failure == 'exception': raise RuntimeError('SECRET')
        if failure == 'timeout': await asyncio.sleep(0)
        if failure == 'cancel': raise asyncio.CancelledError()
        if failure == 'invalid': return None
        return await evaluation.risk_call(plan, wire)
    path = tmp_path / 'risk.json'
    if failure == 'cancel':
        with pytest.raises(asyncio.CancelledError): await evaluation.probe(plan, artifact, path, caller)
    else: await evaluation.probe(plan, artifact, path, caller)
    assert evaluation.audit(plan, artifact) == before
    assert json.loads(path.read_text()) == artifact and 'SECRET' not in path.read_text()
    eligible = [t['calibration'] for t in artifact['trials'] if not t['error_code']]
    if failure in ('input', 'input_budget'): assert not calls
    elif failure in ('budget', 'cancel'): assert len(calls) == 1
    else: assert len(calls) == 10
    assert any(c['risk_probability'] is None and c['risk_not_run_reason'] for c in eligible)
    if failure in ('exception', 'timeout', 'invalid', 'cancel'): assert any(c['risk_error'] for c in eligible)
    if failure == 'ineligible': assert all(len(json.loads(w)['questions']) == 4 and len(json.loads(w)['state']['texts']) == 5 for w in calls)


@pytest.fixture(scope='module')
def ledger():
    from copyeditor.judgment import JudgmentFailure
    plan, cases = evaluation.freeze(1), evaluation.population('calibration')
    by_id = {c['id']: c for c in cases}
    kinds = {c['bad']: c['must_change'] for c in cases}
    original = TypeSafe.evaluate
    async def evaluate(self, wire, **kwargs):
        result, payload = await original(self, wire, **kwargs), json.loads(wire)
        if isinstance(result, JudgmentFailure) or 'texts' not in payload['state']: return result
        return result._replace(blocks=tuple(b._replace(probabilities=tuple((k, (.8 if kinds[payload['state']['texts'][f'b{b.ordinal:04}']['text']] else .6) if k == 'gate' else p) for k, p in b.probabilities)) for b in result.blocks))
    async def collect():
        cache, trials, measurements = {}, [], {}
        for request in plan['request_layouts']:
            key = request['degree'], request['judgment_enabled'], request['layout'], tuple(request['ids'])
            if key not in cache:
                plans = []
                response = await evaluation.adapter.compare_request([by_id[i] for i in request['ids']], *key[:3], 'fixture', plans)
                cache[key] = response, plans
            response, plans = deepcopy(cache[key])
            for ordinal, identity in enumerate(request['ids'], 1):
                row = {k: v for k, v in request.items() if k != 'ids'}
                block = response if request['layout'] == 'text' else response['items'][ordinal - 1]
                calibration = None
                if request['judgment_enabled']:
                    detection, action = block['detection'], block['detection']['action']
                    eligible = detection['status'] == 'eligible'
                    measurement = request['request_id'] + '-risk-0'
                    if eligible: measurements[measurement] = dict(request_id=request['request_id'], batch_index=0, model_calls=1)
                    calibration = dict(gate_probability=detection['gate']['probability'], raw_action=action['selected'], effective_action=action['effective'], action_source=action['source'], confidence=action['confidence'],
                        risk_probability=.85 if eligible else None, risk_error=None, risk_not_run_reason=None if eligible else 'not_eligible', risk_measurement=measurement if eligible else None)
                trials.append(dict(row, example_id=identity, full_response=response, error_code=None, started_at='fixture', latency_ms=1, provider_measurements=response.get('providers'),
                    batch_plans=plans, decision=dict.fromkeys('abcd'), reasons=dict.fromkeys('abcd'), reviewer=None, calibration=calibration))
        return dict(manifest_bytes=evaluation.encoded(plan), manifest_hash=evaluation.legacy.digest(evaluation.encoded(plan).encode()), trials=trials, risk_measurements=measurements)
    with patch.object(TypeSafe, 'evaluate', evaluate): artifact = asyncio.run(collect())
    return plan, artifact


def set_gate(artifact, trial, value):
    from copyeditor.judgment import JudgmentBlockResult, JudgmentChoice, classify_detection
    response = trial['full_response']
    ordinal = 1 if trial['layout'] == 'text' else next(i + 1 for i, t in enumerate([t for t in artifact['trials'] if t['request_id'] == trial['request_id']]) if t is trial)
    block = response if trial['layout'] == 'text' else response['items'][ordinal - 1]
    detection = block['detection']; action = detection['action']
    probabilities = (('gate', value),) + tuple((c['id'], c['probability']) for c in detection['checks'])
    block['detection'] = classify_detection(JudgmentBlockResult(ordinal, probabilities, JudgmentChoice(action['selected'], action['probabilities'], action['confidence'])))
    trial['calibration']['gate_probability'] = value
    return block


def test_calibration_midpoint_variation_and_false_veto_are_recomputed(ledger):
    plan, artifact = deepcopy(ledger)
    before = deepcopy(artifact)
    result = evaluation.calibrate(plan, artifact)
    summary, decision = result['calibration_summary'], result['calibration_decision']
    assert artifact == before and summary['complete']
    assert (summary['natural_max'], summary['unnatural_min'], summary['gap'], decision['derived_floor']) == ('0.6', '0.8', '0.2', '0.7')
    assert summary['planned'] == summary['observed'] == 300 and summary['eligibility_flips'] == 0
    assert len(summary['by_degree_layout_repeat']) == 20 and len(summary['per_case_variation']) == 60
    assert all(v['planned'] == v['observed'] == 5 and v['range'] == '0.0' for v in summary['per_case_variation'].values())
    risk = decision['edit_risk']
    assert risk['high_verified'] == risk['high_pass'] == 300 and risk['not_generated'] == risk['not_eligible'] == 0
    assert risk['high_pass_rate'] == 1 and risk['recommendation'] == 'keep_disabled_false_veto'
    assert decision['reason'] == 'new_threshold_id_contract_hash_and_remeasurement_required' and decision['reviewer'] is None
    assert evaluation.THRESHOLDS[decision['thresholds_version']]['floor'] == .53
    artifact.update(result); artifact['calibration_decision']['reviewer'] = 'owner'
    assert evaluation.calibrate(plan, artifact)['calibration_decision']['reviewer'] == 'owner'


@pytest.mark.parametrize('value,gap,floor', [(.9, '-0.1', None), (.8, '0.0', None), (.7999999999999999, '1E-16', '0.79999999999999995')])
def test_overlap_and_unrounded_adjacent_values_never_move_floor_to_an_edge(ledger, value, gap, floor):
    plan, artifact = deepcopy(ledger)
    for trial in artifact['trials']:
        if trial['judgment_enabled'] and trial['calibration']['gate_probability'] == .6:
            trial['calibration']['gate_probability'] = value
            set_gate(artifact, trial, value)
    result = evaluation.calibrate(plan, artifact)
    assert result['calibration_summary']['gap'] == gap
    assert result['calibration_decision']['derived_floor'] == floor
    if floor is None: assert 'new_policy' in result['calibration_decision']['reason']


@pytest.mark.parametrize('failure', ['missing', 'duplicate', 'references', 'gate', 'bool', 'copy', 'risk', 'link'])
def test_calibration_missing_and_stale_evidence_cannot_become_a_floor_or_low_risk(ledger, failure):
    plan, artifact = deepcopy(ledger)
    trial = next(t for t in artifact['trials'] if t['judgment_enabled'] and t['calibration']['gate_probability'] == .8)
    if failure == 'missing': artifact['trials'].pop()
    elif failure == 'duplicate': artifact['trials'].append(deepcopy(trial))
    elif failure == 'references': plan['references_hash'] = '0' * 64
    elif failure in ('gate', 'bool'): trial['calibration']['gate_probability'] = None if failure == 'gate' else True
    elif failure == 'copy': trial['calibration']['effective_action'] = 'preserve_as_is'
    elif failure == 'risk': trial['calibration'].update(risk_probability=None, risk_error='probe_error', risk_not_run_reason='probe_failed')
    else: trial['calibration']['risk_measurement'] = 'unknown'
    if failure in ('missing', 'duplicate', 'references'):
        with pytest.raises(ValueError): evaluation.calibrate(plan, artifact)
    else:
        result = evaluation.calibrate(plan, artifact)
        if failure in ('gate', 'bool', 'copy'):
            assert not result['calibration_summary']['complete'] and result['calibration_decision']['derived_floor'] is None
            assert result['calibration_summary']['planned'] == 300 and result['calibration_summary']['observed'] == 299
        assert result['calibration_decision']['edit_risk']['missing'] == 1
        assert not result['calibration_decision']['edit_risk']['complete']


def test_risk_empty_denominator_never_auto_adopts_and_reviewer_is_invalidated(ledger):
    plan, artifact = deepcopy(ledger)
    artifact.update(evaluation.calibrate(plan, artifact)); artifact['calibration_decision']['reviewer'] = 'owner'
    for trial in artifact['trials']:
        if trial['calibration'] and trial['calibration']['risk_probability'] is not None: trial['calibration']['risk_probability'] = .79
    result = evaluation.calibrate(plan, artifact)
    assert result['calibration_decision']['reviewer'] is None
    risk = result['calibration_decision']['edit_risk']
    assert risk['complete'] and risk['high_verified'] == 0 and risk['high_pass_rate'] is None and risk['recommendation'] == 'unconfirmed'


def test_repetition_flips_and_confidence_do_not_select_thresholds(ledger):
    plan, artifact = deepcopy(ledger)
    trial = next(t for t in artifact['trials'] if t['judgment_enabled'] and t['degree'] == 'polish' and t['layout'] == 'text' and t['calibration']['gate_probability'] == .6)
    block = set_gate(artifact, trial, .52)
    block.update(editing='not_run', verification=dict(status='not_run', reason='not_generated', checks=[]))
    block['providers'][0].update(model_calls=0, estimation_calls=0, latency_ms=0)
    block['providers'][1]['model_calls'] = 1
    trial['calibration'].update(risk_probability=None, risk_measurement=None, risk_not_run_reason='not_eligible')
    result = evaluation.calibrate(plan, artifact)
    variation = result['calibration_summary']['per_case_variation']['polish/text/' + trial['example_id']]
    assert (variation['min'], variation['max'], variation['range'], variation['transitions']) == ('0.52', '0.6', '0.08', 1)
    assert result['calibration_summary']['eligibility_flips'] == 1 and result['calibration_summary']['false_positives'] == 99
    trial['calibration']['confidence'] = block['detection']['action']['confidence'] = 0
    assert evaluation.calibrate(plan, artifact) == result


@pytest.mark.parametrize('outcome', ['fail', 'indeterminate', 'no_issue', 'preservation'])
def test_high_risk_boundary_denominator_and_non_generation_exclusions(ledger, outcome):
    from copyeditor.judgment import JudgmentBlockResult, classify_verification
    plan, artifact = deepcopy(ledger)
    for t in artifact['trials']:
        if t['calibration']: t['calibration']['risk_probability'] = .79
    trial = next(t for t in artifact['trials'] if t['judgment_enabled'] and t['degree'] == 'rewrite' and t['layout'] == 'text' and t['calibration']['gate_probability'] == .6)
    trial['calibration']['risk_probability'] = .80
    block = trial['full_response']
    if outcome in ('fail', 'indeterminate'):
        checks = block['verification']['checks']
        block['verification'] = classify_verification(JudgmentBlockResult(1, tuple((c['id'], .1 if outcome == 'fail' else .5) for c in checks), None))
        block['flag'] = dict(kind='verification_rejected', reason='Candidate verification failed.' if outcome == 'fail' else 'Candidate verification was inconclusive.', checks=[c['id'] for c in checks])
    else:
        block['providers'][1]['model_calls'] = 1
        block['verification'] = dict(status='not_run', reason='not_generated' if outcome == 'no_issue' else 'preservation_rejected', checks=[])
        if outcome == 'no_issue':
            block.update(editing='diagnosed_no_issue', diagnosis=dict(status='no_issue', expression=None, reason=None)); block['providers'][0]['model_calls'] = 1
        else:
            block.update(flag=dict(kind='rejected', reason='Preservation checks failed.', checks=['numbers']), regenerated=True); block['providers'][0]['model_calls'] = 3
    result = evaluation.calibrate(plan, artifact)
    assert result['calibration_summary']['complete']
    risk = result['calibration_decision']['edit_risk']
    assert risk['high'] == 1 and risk['high_pass'] == 0 and risk['complete']
    assert risk['high_verified'] == (1 if outcome in ('fail', 'indeterminate') else 0)
    assert risk['recommendation'] == ('owner_review_required' if outcome in ('fail', 'indeterminate') else 'unconfirmed')


def test_calibration_cli_overwrites_stale_decisions_without_changing_thresholds(ledger, tmp_path):
    plan, artifact = deepcopy(ledger)
    frozen, path = tmp_path / 'plan.json', tmp_path / 'evaluation.json'
    evaluation.legacy.save(frozen, plan); evaluation.legacy.save(path, artifact)
    command = [sys.executable, 'scripts/judgment_evaluation.py', 'calibrate', '--plan', str(frozen), '--artifact', str(path)]
    assert evaluation.legacy.subprocess.run(command, capture_output=True).returncode == 0
    assert json.loads(path.read_text())['calibration_decision']['derived_floor'] == '0.7'
    artifact['trials'].pop(); evaluation.legacy.save(path, artifact)
    assert evaluation.legacy.subprocess.run(command, capture_output=True).returncode == 1
    assert json.loads(path.read_text())['calibration_decision'] is None
