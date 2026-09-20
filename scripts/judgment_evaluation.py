"""Freeze and run offline or explicitly opted-in live judgment comparisons; never accept quality."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from time import monotonic
import benchmark_provider as adapter
import rewrite_evaluation as legacy
from copyeditor.judgment import POLICIES, THRESHOLDS, REFERENCES, definition_hash, _json_value
from copyeditor.rules import load_rules
from examples_to_promptfoo import load_examples

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
        references_hash=definition_hash(REFERENCES), packing_version=policy['packing_version'], risk_probe=None,
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
    return artifact


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
