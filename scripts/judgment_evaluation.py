"""Freeze and run offline or explicitly opted-in live judgment comparisons; never accept quality."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from time import monotonic
import benchmark_provider as adapter
import rewrite_evaluation as legacy
from copyeditor.judgment import POLICIES, THRESHOLDS, REFERENCES, definition_hash, _json_value
from copyeditor.judgment import JudgmentInput, JudgmentBlock, JudgmentFailure
from copyeditor.judgment_batch import prepare_judgments
from copyeditor.providers.base import Background
from copyeditor.providers.typesafe import TypeSafe
from copyeditor.metrics import Metrics
from copyeditor.rules import load_rules
from examples_to_promptfoo import load_examples

RISK = dict(version='edit-risk-probe-v2', high_boundary=0.80, max_calls=300,
            input_budget=19200000, timeout_seconds=10,
            question='Would attempting the selected editing action be more likely to lose important meaning, nuance or appropriate register than to improve this text?')

SETS = dict(calibration="calibration-v2", acceptance="judgment-acceptance-v2", existing="existing-rewrite-v1", regression="packing-regression-v1")


def encoded(value):
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def population(name):
    name = next((k for k, v in SETS.items() if v == name), name)
    cases = {c['id']: c for c in load_examples(legacy.ROOT / 'examples', load_rules(legacy.ROOT / 'rules', None)) if c['language'] == 'ja'}
    prefix, count = {'calibration': ('judgment-calibration', 15), 'acceptance': ('judgment-acceptance', 20), 'existing': ('rewrite', 24)}.get(name, ('', 0))
    if name == 'regression':
        return [dict(cases['judgment-calibration-01'], id=f'packing-{i}', bad=str(i) + 'あ' * 2390, good=str(i) + 'あ' * 2390, must_change=True) for i in range(1, 6)]
    return [cases[f'{prefix}-{i:02}'] for i in range(1, count + 1)]


def freeze(revision, mode='fixture', name='calibration', created_at=None):
    name = next((k for k, v in SETS.items() if v == name), name)
    if type(revision) is not int or revision < 1 or mode not in ('fixture', 'live') or name not in ('calibration', 'acceptance', 'existing', 'regression'): raise ValueError('Invalid comparison plan')
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    cases = population(name)
    config, snapshot, _ = adapter.comparison_environment(cases[0], mode, False)
    policy, threshold = POLICIES[config['judgment.policy_version']], THRESHOLDS[config['judgment.thresholds_version']]
    entries = [dict(id=c['id'], path=None if name == 'regression' else f"examples/ja/{c['id']}.yaml",
                    sha256=legacy.digest(encoded(c).encode() if name == 'regression' else (legacy.ROOT / f"examples/ja/{c['id']}.yaml").read_bytes()), input_hash=legacy.digest(encoded(c).encode()), kind='problem' if c['must_change'] else 'natural') for c in cases]
    layouts = {'text': [[c['id']] for c in cases]}
    if name == 'calibration': layouts['items'] = [[c['id'] for c in cases[i:i + 5]] for i in (0, 5, 10)]
    if name == 'regression': layouts = {'items': [[c['id'] for c in cases]], 'reversed': [[c['id'] for c in reversed(cases)]]}
    conditions, requests, trials = [], [], []
    for degree in ('polish', 'rewrite'):
        for enabled in (False, True):
            conditions.append(dict(degree=degree, judgment_enabled=enabled, editing_model=config['model'], thinking=config['thinking'],
                rules_version=snapshot.rules_version, common_version=snapshot.common_version, prompt_hash=legacy.digest((legacy.ROOT / 'src/copyeditor/prompt.py').read_bytes()),
                judgment_model=config['judgment.model'] if enabled else None, policy_version=config['judgment.policy_version'] if enabled else None,
                policy_hash=definition_hash(policy) if enabled else None, thresholds_version=config['judgment.thresholds_version'] if enabled else None,
                thresholds_hash=definition_hash(threshold) if enabled else None))
        for repeat in range(1, 6):
            for layout, groups in layouts.items():
                for index, ids in enumerate(groups):
                    for enabled in ((False, True) if repeat % 2 else (True, False)):
                        row = dict(set=SETS[name], layout=layout, repeat=repeat, degree=degree, judgment_enabled=enabled,
                                   request_id=f'{revision}-{legacy.digest(created_at.encode())[:12]}-{name}-{degree}-{repeat}-{layout}-{index}-{int(enabled)}')
                        requests.append(dict(row, ids=ids))
                        trials.extend(dict(row, example_id=identity) for identity in ids)
    return json.loads(encoded(dict(evaluation_revision=revision, mode=mode, source_commit=legacy.subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=legacy.ROOT, text=True).strip(),
        created_at=created_at, sets=[dict(name=SETS[name], role='calibration' if name == 'calibration' else 'regression' if name == 'regression' else 'acceptance', cases=entries, repeats=5)],
        conditions=conditions, acceptance_criteria=dict(legacy=legacy.THRESHOLDS, quality_accepted=False, human_review='required'),
        planned_trials=trials, request_layouts=requests, calibration_run_budget=dict(blocks=600, requests=360) if name == 'calibration' else None,
        references_hash=definition_hash(REFERENCES), packing_version=policy['packing_version'], risk_probe=RISK if name == 'calibration' else None,
        config={k: v for k, v in config.items() if not k.startswith('auth.') and k != 'judgment.enabled'})))


def check_plan(plan):
    if encoded(plan) != encoded(freeze(plan['evaluation_revision'], plan['mode'], plan['sets'][0]['name'], plan['created_at'])): raise ValueError('Comparison inputs changed')


def audit(plan, artifact):
    check_plan(plan)
    if artifact['manifest_bytes'] != encoded(plan) or artifact['manifest_hash'] != legacy.digest(encoded(plan).encode()): raise ValueError('Manifest replaced')
    keys = tuple(plan['planned_trials'][0])
    key = lambda t: encoded({k: t[k] for k in keys})
    if Counter(map(key, artifact['trials'])) != Counter(map(key, plan['planned_trials'])): raise ValueError('Missing, duplicate or moved trials')
    requests = {}
    for trial in artifact['trials']:
        if trial['full_response'] is None and not trial['error_code']: raise ValueError('Unmarked missing response')
        if trial['started_at'] is None or trial['latency_ms'] is None: raise ValueError('Unexecuted observation')
        if trial['error_code'] == 'not_run': raise ValueError('Unexecuted trial')
        observation = {k: trial[k] for k in ('full_response', 'error_code', 'provider_measurements', 'latency_ms', 'batch_plans', 'started_at')}
        previous = requests.setdefault(trial['request_id'], observation)
        if previous != observation: raise ValueError('Inconsistent request observation')
    return requests  # Aggregate calls and costs once per request, never once per block.


async def run(plan, output, runner=adapter.compare_request):
    check_plan(plan)
    artifact = dict(manifest_bytes=encoded(plan), manifest_hash=legacy.digest(encoded(plan).encode()), trials=[dict(t,
        started_at=None, full_response=None, error_code='not_run', provider_measurements=None, latency_ms=None, batch_plans=[],
        decision=dict.fromkeys('abcd'), reasons=dict.fromkeys('abcd'), reviewer=None, calibration=None) for t in plan['planned_trials']])
    legacy.save(output, artifact)
    cases = {c['id']: c for c in population(plan['sets'][0]['name'])}
    for request in plan['request_layouts']:
        plans, response, error, cancelled = [], None, None, False
        started, clock = datetime.now(timezone.utc).isoformat(), monotonic()
        try:
            response = await runner([cases[i] for i in request['ids']], request['degree'], request['judgment_enabled'], request['layout'], plan['mode'], plans)
            if not isinstance(response, dict): raise ValueError('Missing response')
            error = response.get('error', {}).get('code')
        except (Exception, asyncio.CancelledError) as failure:
            cancelled = isinstance(failure, asyncio.CancelledError)
            response, error = None, 'evaluation_error'
        measurements = None if response is None else response.get('providers', [dict(role='editing', provider='vertex', model=response.get('model'),
            **{k: response.get(k) for k in ('model_calls', 'estimation_calls', 'usage', 'cost', 'latency_ms')})])
        elapsed = round((monotonic() - clock) * 1000)
        for trial in artifact['trials']:
            if trial['request_id'] == request['request_id']:
                trial.update(started_at=started, full_response=response, error_code=error, provider_measurements=measurements,
                             latency_ms=elapsed, batch_plans=json.loads(encoded(plans)))
                if trial['set'] == SETS['calibration'] and trial['judgment_enabled']:
                    block = next((i for i in (response or {}).get('items', []) if i['id'] == f"b{request['ids'].index(trial['example_id']) + 1:04}"), response or {})
                    detection = block.get('detection', {}); action = detection.get('action') or {}
                    trial['calibration'] = dict(gate_probability=(detection.get('gate') or {}).get('probability'), raw_action=action.get('selected'),
                        effective_action=action.get('effective'), action_source=action.get('source'), confidence=action.get('confidence'),
                        risk_probability=None, risk_error=None, risk_not_run_reason=None, risk_measurement=None)
        legacy.save(output, artifact)
        if cancelled: raise asyncio.CancelledError
    audit(plan, artifact)
    await probe(plan, artifact, output)
    return artifact


def risk_payloads(plan, request, trials):
    cases = {c['id']: c for c in population(request['set'])}
    first = cases[request['ids'][0]]
    background = Background(*(first['background'].get(k, '') for k in Background._fields))
    data = JudgmentInput('detect', 'ja', first['format'], background, background.tone,
                         tuple(JudgmentBlock(i, cases[key]['bad'], '', None, None) for i, key in enumerate(request['ids'], 1)))
    policy = POLICIES[plan['config']['judgment.policy_version']]
    prepared = prepare_judgments(data, policy_id=plan['config']['judgment.policy_version'])
    expected = dict(version=prepared.plan.version, phase='detect', batches=[b._asdict() for b in prepared.plan.batches])
    if encoded(expected) not in [encoded(p) for p in trials[0]['batch_plans']]: raise ValueError('Detection batch mismatch')
    by_id = {t['example_id']: t for t in trials}
    for index, (wire, batch) in enumerate(zip(prepared.requests, prepared.plan.batches)):
        payload, targets = json.loads(wire), {}
        for ordinal in batch.ordinals:
            trial = by_id[request['ids'][ordinal - 1]]
            if trial['calibration']['risk_not_run_reason'] != 'pending': continue
            action = trial['calibration']['effective_action']
            block_id = f'b{ordinal:04}'
            targets[block_id + '.edit_risk'] = (trial, dict(type='noul', instructions=policy['instruction_separator'].join([
                policy['prefix'], policy['formats'][first['format']], policy['targets']['detect'].format(block_id=block_id),
                RISK['question'], action, policy['action_instructions'][action]])))
        if targets:
            payload['questions'] = {key: value[1] for key, value in targets.items()}
            yield index, payload, {int(key[1:5]): value[0] for key, value in targets.items()}


async def risk_call(plan, wire):
    import httpx
    _, _, secret = adapter.comparison_environment(population('calibration')[0], plan['mode'], True)
    def reply(request):
        questions = json.loads(request.content)['questions']
        return httpx.Response(200, json=dict(model='jev-1.13.0', answers={k: dict(type='noul', noul=.85) for k in questions},
                                             usage=dict(input_tokens=0, output_tokens=0)))
    client = TypeSafe(secret, transport=httpx.MockTransport(reply) if plan['mode'] == 'fixture' else None)
    try:
        return await client.evaluate(wire, remaining_seconds=RISK['timeout_seconds'])
    finally:
        await client.aclose()


async def probe(plan, artifact, output, caller=risk_call):
    audit(plan, artifact)  # Probes cannot precede any planned production request.
    if plan['risk_probe'] is None: return
    if 'risk_measurements' in artifact: raise ValueError('Probe already attempted')
    artifact['risk_complete'], artifact['run_cost'] = False, None
    measurements = artifact['risk_measurements'] = {}
    calls, units = 0, 0
    for trial in artifact['trials']:
        calibration = trial['calibration']
        if calibration is None: continue
        request = next(r for r in plan['request_layouts'] if r['request_id'] == trial['request_id'])
        response = trial['full_response'] or {}
        block = next((b for b in response.get('items', []) if b['id'] == f"b{request['ids'].index(trial['example_id']) + 1:04}"), response)
        detection = block.get('detection', {})
        calibration['risk_not_run_reason'] = ('production_error' if trial['error_code'] else 'missing_detection' if not detection
            else 'not_eligible' if detection.get('status') != 'eligible' else 'pending')
    legacy.save(output, artifact)
    for request in plan['request_layouts']:
        trials = [t for t in artifact['trials'] if t['request_id'] == request['request_id'] and t['calibration'] is not None]
        pending = [t for t in trials if t['calibration']['risk_not_run_reason'] == 'pending']
        if not pending: continue
        try:
            payloads = list(risk_payloads(plan, request, trials))
        except Exception:
            for t in pending: t['calibration'].update(risk_error='probe_input_error', risk_not_run_reason='input_mismatch')
            legacy.save(output, artifact)
            continue
        for index, payload, targets in payloads:
            wire = encoded(payload).encode()
            reserved = len(wire) + 4096
            largest = len(encoded(payload['state']).encode()) + max(len(encoded(q).encode()) for q in payload['questions'].values()) + 4096
            if reserved > 64000 or largest > 32000 or calls >= min(300, plan['risk_probe']['max_calls']) or units + reserved > plan['risk_probe']['input_budget']:
                for t in targets.values(): t['calibration']['risk_not_run_reason'] = 'request_budget'
                continue
            calls += 1; units += reserved
            identity = request['request_id'] + f'-risk-{index}'
            meter = Metrics(monotonic(), 'jev-1.13.0', plan['config']['judgment.pricing'])
            slot = meter.start_call(is_regeneration=False)
            error, values, cancelled = None, {}, False
            try:
                async with asyncio.timeout(plan['risk_probe']['timeout_seconds']): result = await caller(plan, wire)
                meter.record_usage(slot, result.usage)
                if isinstance(result, JudgmentFailure) or result.model != 'jev-1.13.0': raise ValueError('Probe failed')
                values = {b.ordinal: dict(b.probabilities)['edit_risk'] for b in result.blocks}
                if set(values) != set(targets) or any(type(p) not in (float, int) or not 0 <= p <= 1 for p in values.values()): raise ValueError('Invalid probe')
            except (Exception, asyncio.CancelledError) as failure:
                cancelled = isinstance(failure, asyncio.CancelledError)
                error = 'probe_cancelled' if cancelled else 'probe_timeout' if isinstance(failure, TimeoutError) else 'probe_error'
            measurement = dict(meter.snapshot(), request_id=request['request_id'], batch_index=index, payload_hash=legacy.digest(wire), input_units=reserved)
            measurements[identity] = measurement
            for ordinal, trial in targets.items():
                trial['calibration'].update(risk_probability=None if error else values[ordinal], risk_error=error,
                    risk_not_run_reason='probe_failed' if error else None, risk_measurement=identity)
            legacy.save(output, artifact)
            if cancelled: raise asyncio.CancelledError
    artifact['risk_complete'] = all(t['calibration'] is None or t['calibration']['risk_not_run_reason'] in (None, 'not_eligible') for t in artifact['trials'])
    costs = [r['full_response'].get('cost') if r['full_response'] else None for r in audit(plan, artifact).values()]
    costs += [m['cost'] for m in measurements.values()]
    artifact['run_cost'] = (dict(amount=format(sum(Decimal(c['amount']) for c in costs), 'f'), currency=costs[0]['currency'])
        if costs and all(c is not None for c in costs) and len({c['currency'] for c in costs}) == 1 else None)
    legacy.save(output, artifact)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('plan', 'run', 'check'))
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--artifact', type=Path, default=Path('judgment-evaluation.json'))
    parser.add_argument('--revision', type=int, default=1)
    parser.add_argument('--mode', choices=('fixture', 'live'), default='fixture')
    parser.add_argument('--set', dest='name', choices=('calibration', 'acceptance', 'existing', 'regression'), default='calibration')
    args = parser.parse_args()
    if args.operation == 'plan': return legacy.save(args.plan, freeze(args.revision, args.mode, args.name))
    plan = json.loads(args.plan.read_text())
    if args.mode != plan['mode']: parser.error('Mode must match frozen plan; live requires --mode live')
    if args.operation == 'run': asyncio.run(run(plan, args.artifact))
    else: print(len(audit(plan, json.loads(args.artifact.read_text()))), 'request observations; quality unreviewed')


if __name__ == '__main__':
    main()
