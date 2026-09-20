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
