"""Freeze and run offline or explicitly opted-in live judgment comparisons; never accept quality."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import json
from pathlib import Path
from time import monotonic
import benchmark_provider as adapter
import rewrite_evaluation as legacy
from copyeditor.judgment import definition_hash, _json_value
from copyeditor.judgment_v2 import POLICIES, REFERENCES, snapshot as judgment_snapshot
from copyeditor.edit_protocol import validate_final
from copyeditor.providers.base import SourceItem
from copyeditor.rules import load_rules
from examples_to_promptfoo import load_examples

COMPARISON = dict(acceptance=dict(problem=30, natural=10, per_example=4, per_repeat=24),
                  existing=dict(problem=18, natural=6, per_example=4, per_repeat=15), repeats=5, required_gain=1)

SETS = dict(calibration="calibration-v3", acceptance="judgment-acceptance-v3", existing="existing-rewrite-v4", regression="packing-regression-v4")


def encoded(value):
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def population(name):
    name = next((k for k, v in SETS.items() if v == name), name)
    cases = {c['id']: c for c in load_examples(legacy.ROOT / 'examples', load_rules(legacy.ROOT / 'rules', None)) if c['language'] == 'ja'}
    prefix, count = {'calibration': ('judgment-calibration', 30), 'acceptance': ('judgment-acceptance', 40), 'existing': ('rewrite', 24)}.get(name, ('', 0))
    if name == 'regression':
        return [dict(cases['judgment-calibration-01'], id=f'packing-{i}', bad=str(i) + 'あ' * 2390, good=str(i) + 'あ' * 2390, must_change=True) for i in range(1, 6)]
    return [cases[f'{prefix}-{i:02}'] for i in range(1, count + 1)]


def freeze(revision, mode='fixture', name='existing', created_at=None, pairs=None):
    name = next((k for k, v in SETS.items() if v == name), name)
    if type(revision) is not int or revision < 1 or mode not in ('fixture', 'live') or name not in ('calibration', 'acceptance', 'existing', 'regression'): raise ValueError('Invalid comparison plan')
    if name in ('calibration', 'acceptance') or pairs is not None:
        raise ValueError('Generation-four evaluation population and owner manifest are not complete')
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    cases = population(name)
    config, snapshot, _ = adapter.comparison_environment(cases[0], mode, False)
    selected_config, _, _ = adapter.comparison_environment(cases[0], mode, True)
    registry = adapter.fixture_registry if mode == 'fixture' else judgment_snapshot
    selected = registry(selected_config['judgment.policy_version'], selected_config['judgment.thresholds_version'])
    policy, threshold = selected.policy, selected.threshold
    entries = [dict(id=c['id'], path=None if name == 'regression' else f"examples/ja/{c['id']}.yaml",
                    sha256=legacy.digest(encoded(c).encode() if name == 'regression' else (legacy.ROOT / f"examples/ja/{c['id']}.yaml").read_bytes()), input_hash=legacy.digest(encoded(c).encode()), kind='problem' if c['must_change'] else 'natural') for c in cases]
    layouts = {'text': [[c['id']] for c in cases]}
    if name == 'calibration': layouts['items'] = [[c['id'] for c in cases[i:i + 5]] for i in range(0, 30, 5)]
    if name == 'regression': layouts = {'items': [[c['id'] for c in cases]], 'reversed': [[c['id'] for c in reversed(cases)]]}
    conditions, requests, trials = [], [], []
    for degree in ('polish', 'rewrite'):
        for enabled in (False, True):
            conditions.append(dict(degree=degree, judgment_enabled=enabled, editing_model=config['model'], thinking=config['thinking'],
                rules_version=snapshot.rules_version, common_version=snapshot.common_version, prompt_hash=legacy.digest((legacy.ROOT / 'src/copyeditor/prompt.py').read_bytes()),
                judgment_model=config['judgment.model'] if enabled else None, policy_version=config['judgment.policy_version'] if enabled else None,
                policy_hash=definition_hash(policy) if enabled else None, thresholds_version=selected.threshold['id'] if enabled else None,
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
        conditions=conditions, acceptance_criteria=dict(legacy=legacy.THRESHOLDS, comparison=COMPARISON, quality_accepted=False, human_review='required'),
        planned_trials=trials, request_layouts=requests, calibration_run_budget=dict(blocks=1200, requests=720) if name == 'calibration' else None,
        references_hash=definition_hash(REFERENCES), packing_version=policy['packing_version'],
        config={k: v for k, v in config.items() if not k.startswith('auth.') and k != 'judgment.enabled'})))


def check_plan(plan):
    if encoded(plan) != encoded(freeze(plan['evaluation_revision'], plan['mode'], plan['sets'][0]['name'], plan['created_at'], (plan.get('verification') or {}).get('pairs'))): raise ValueError('Comparison inputs changed')


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
        if trial['full_response'] is not None and trial['provider_measurements'] != trial['full_response'].get('providers'):
            raise ValueError('Provider measurements replaced')
        observation = {k: trial[k] for k in ('full_response', 'error_code', 'provider_measurements', 'latency_ms', 'batch_plans', 'started_at')}
        previous = requests.setdefault(trial['request_id'], observation)
        if previous != observation: raise ValueError('Inconsistent request observation')
    return requests  # Aggregate calls and costs once per request, never once per block.


async def run(plan, output, runner=adapter.compare_request):
    check_plan(plan)
    artifact = dict(manifest_bytes=encoded(plan), manifest_hash=legacy.digest(encoded(plan).encode()), trials=[dict(t,
        started_at=None, full_response=None, error_code='not_run', provider_measurements=None, latency_ms=None, batch_plans=[],
        decision=dict.fromkeys('abcd'), reasons=dict.fromkeys('abcd'), reviewer=None) for t in plan['planned_trials']])
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
        legacy.save(output, artifact)
        if cancelled: raise asyncio.CancelledError
    audit(plan, artifact)
    return artifact


def summarize(plan, artifact):
    try:
        observations = audit(plan, artifact)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('Invalid comparison inventory or manifest') from error
    cases = {c['id']: c for c in population(plan['sets'][0]['name'])}
    _, snapshot, _ = adapter.comparison_environment(next(iter(cases.values())), plan['mode'], False)
    requests = {r['request_id']: r for r in plan['request_layouts']}
    conditions = {(c['degree'], c['judgment_enabled']): c for c in plan['conditions']}
    invalid, groups, rows = set(), {}, []
    for identity, observation in observations.items():
        request, response = requests[identity], observation['full_response']
        condition = conditions[request['degree'], request['judgment_enabled']]
        originals = tuple(SourceItem('text' if request['layout'] == 'text' else f'b{i:04}', cases[key]['bad'], '') for i, key in enumerate(request['ids'], 1))
        try:
            if response is None or response.get('schema_version') != 4: raise ValueError()
            if any(response.get(k) != condition[k] for k in ('rules_version', 'common_version')): raise ValueError()
            if response.get('degree', 'polish') != request['degree'] or response.get('language') != 'ja': raise ValueError()
            if request['judgment_enabled']:
                if any(response[k] != condition[k] for k in ('policy_version', 'thresholds_version', 'policy_hash', 'thresholds_hash')): raise ValueError()
                if [r['model'] for r in response['providers']] != [condition['editing_model'], condition['judgment_model']]: raise ValueError()
            if response['providers'][0]['model'] != condition['editing_model']: raise ValueError()
            validate_final(response, originals, expected_enabled=request['judgment_enabled'],
                registry=adapter.fixture_registry if plan['mode'] == 'fixture' else judgment_snapshot,
                format=cases[request['ids'][0]]['format'])
            if response.get('error', {}).get('code') != observation['error_code'] or observation['error_code'] in ('invalid_response', 'html_structure'): raise ValueError()
            if response.get('status') == 'ok' and [b.get('id', 'text') for b in response.get('items', [response])] != [o.id for o in originals]: raise ValueError()
        except (ValueError, TypeError, KeyError):
            invalid.add(identity)
    for trial in artifact['trials']:
        case, response = cases[trial['example_id']], trial['full_response'] or {}
        request, condition = requests[trial['request_id']], conditions[trial['degree'], trial['judgment_enabled']]
        block_id = 'text' if trial['layout'] == 'text' else f"b{request['ids'].index(trial['example_id']) + 1:04}"
        block = next((b for b in response.get('items', [response]) if b.get('id', 'text') == block_id), {})
        decision, reasons, problem = trial['decision'], trial['reasons'], case['must_change']
        required = ('a', 'b', 'c') if problem else ('b', 'c', 'd')
        unused = 'd' if problem else 'a'
        if set(decision) != set('abcd') or set(reasons) != set('abcd') or decision[unused] is not None or reasons[unused] is not None: raise ValueError('Invalid judgment targets')
        if any(v is not None and type(v) is not bool for v in decision.values()): raise ValueError('Invalid human judgment')
        text = block.get('text')
        if text is None and any(decision[k] is not None for k in ('b', 'c')): raise ValueError('Judgment without returned text')
        required = tuple(k for k in required if text is not None or k not in ('b', 'c'))
        pending = not isinstance(trial['reviewer'], str) or not trial['reviewer'].strip() or any(decision[k] is None or not isinstance(reasons[k], str) or not reasons[k].strip() for k in required)
        healthy = response.get('status') == 'ok' and not trial['error_code'] and trial['request_id'] not in invalid
        changed = text is not None and text != case['bad']
        failures = []
        if trial['request_id'] in invalid: failures.append('response_integrity')
        if healthy:
            protected = snapshot.languages['ja'].protected_terms + (tuple(case['protected_terms']) if plan['mode'] == 'fixture' else ())
            ratio = {k: plan['config']['length_ratio.' + k] for k in ('min', 'max')}
            if legacy.preservation.check(case['bad'], text, protected, ratio, 'text' if case['format'] == 'html' else case['format']).failed: failures.append('preservation')
        counts = dict(planned=1, problem=int(problem), natural=int(not problem), pending=int(pending),
            meaning_violations=int(decision['b'] is False), unnecessary_changes=int(decision['c'] is False),
            natural_changes=int(not problem and changed), natural_failures=int(not problem and (not healthy or changed or bool(block.get('flag')) or decision['d'] is False)),
            errors=int(bool(trial['error_code']) or response.get('status') != 'ok'), flags=int(bool(block.get('flag'))), integrity=len(failures),
            detection_misses=int(problem and (block.get('detection') or {}).get('status') == 'insufficient'),
            detection_false_positives=int(not problem and (block.get('detection') or {}).get('status') == 'eligible'))
        improved = bool(problem and healthy and changed and not block.get('flag') and not failures and all(decision[k] is True for k in required) and not pending)
        counts['improved'] = int(improved)
        key = '/'.join((trial['degree'], 'on' if trial['judgment_enabled'] else 'off', trial['layout']))
        group = groups.setdefault(key, dict(counts=Counter(), per_example={}, per_repeat={}, request_ids=set()))
        group['counts'].update(counts); group['request_ids'].add(trial['request_id'])
        if problem:
            for field, identity in (('per_example', trial['example_id']), ('per_repeat', str(trial['repeat']))):
                tally = group[field].setdefault(identity, dict(planned=0, improved=0))
                tally['planned'] += 1; tally['improved'] += improved
        failures += [k for k in ('pending', 'meaning_violations', 'unnecessary_changes', 'natural_failures', 'errors', 'flags') if counts[k]]
        if problem and not improved: failures.append('not_improved')
        rows.append(dict(request_id=trial['request_id'], example_id=trial['example_id'], decision=decision, reasons=reasons, reviewer=trial['reviewer'], failures=failures))
    name = plan['sets'][0]['name']
    thresholds = COMPARISON['acceptance' if name == SETS['acceptance'] else 'existing']
    for key, group in groups.items():
        count = group['counts']
        absolute = not any(count[k] for k in ('meaning_violations', 'unnecessary_changes', 'natural_failures', 'integrity', 'pending'))
        rates = all(t['improved'] >= thresholds['per_example'] for t in group['per_example'].values()) and all(t['improved'] >= thresholds['per_repeat'] for t in group['per_repeat'].values())
        group['criteria_met'] = absolute and (rates if name == SETS['acceptance'] or name == SETS['existing'] and key.startswith('rewrite/') else True)
        group['reasons'] = [k for k in ('meaning_violations', 'unnecessary_changes', 'natural_failures', 'integrity', 'pending') if count[k]]
        if not group['criteria_met'] and not group['reasons']: group['reasons'].append('problem_rates')
        group['problem_rate'] = count['improved'] / count['problem'] if count['problem'] else None
        measured = [observations[i] for i in sorted(group.pop('request_ids'))]
        group.update(requests=len(measured), latency_ms=sum(r['latency_ms'] for r in measured))
        costs = [r['full_response'].get('cost') if r['full_response'] else None for r in measured]
        group['cost'] = (dict(amount=format(sum(Decimal(c['amount']) for c in costs), 'f'), currency=costs[0]['currency'])
            if costs and all(c is not None for c in costs) and len({c['currency'] for c in costs}) == 1 else None)
    comparison, gains = {}, 0
    for key in groups:
        if '/on/' not in key: continue
        on, off = groups[key]['counts'], groups[key.replace('/on/', '/off/')]['counts']
        comparison[key] = dict(non_regression=all(on[k] <= off[k] for k in ('meaning_violations', 'unnecessary_changes', 'natural_changes')) and on['improved'] >= off['improved'],
            delta={k: on[k] - off[k] for k in ('meaning_violations', 'unnecessary_changes', 'natural_changes', 'improved')})
        gains += max(0, off['natural_changes'] - on['natural_changes']) + max(0, off['unnecessary_changes'] - on['unnecessary_changes']) + max(0, on['improved'] - off['improved'])
    complete = not any(g['counts']['pending'] or g['counts']['integrity'] for g in groups.values())
    legacy_met = groups.get('rewrite/off/text', {}).get('criteria_met') if name == SETS['existing'] else None
    eligible = name in (SETS['acceptance'], SETS['existing'])
    passed = complete and eligible and all(groups[k]['criteria_met'] and v['non_regression'] for k, v in comparison.items()) and legacy_met is not False
    reason = 'unreviewed_or_invalid' if not complete else 'not_acceptance_population' if not eligible else 'criteria_failed' if not passed else 'no_added_value' if name == SETS['acceptance'] and gains < COMPARISON['required_gain'] else 'criteria_met'
    return dict(set=name, groups=groups, comparison=comparison, judgments=rows, complete=complete, added_value=gains,
                legacy_criteria_met=legacy_met, status=reason, criteria_met=reason == 'criteria_met', quality_accepted=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('plan', 'run', 'check', 'report', 'calibrate', 'verify', 'verify-report'))
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--artifact', type=Path, default=Path('judgment-evaluation.json'))
    parser.add_argument('--revision', type=int, default=1)
    parser.add_argument('--mode', choices=('fixture', 'live'), default='fixture')
    parser.add_argument('--set', dest='name', choices=('calibration', 'acceptance', 'existing', 'regression'), default='existing')
    parser.add_argument('--pairs', type=Path)
    args = parser.parse_args()
    if args.operation == 'plan': return legacy.save(args.plan, freeze(args.revision, args.mode, args.name, pairs=json.loads(args.pairs.read_text()) if args.pairs else None))
    plan = json.loads(args.plan.read_text())
    if args.mode != plan['mode']: parser.error('Mode must match frozen plan; live requires --mode live')
    if args.operation == 'run': asyncio.run(run(plan, args.artifact))
    elif args.operation in ('verify', 'verify-report', 'calibrate'):
        parser.error('Generation-four calibration requires a complete population and owner manifest')
    elif args.operation == 'report':
        artifact = json.loads(args.artifact.read_text())
        try: result = summarize(plan, artifact)
        except (ValueError, TypeError, KeyError):
            result = dict(status='invalid_artifact', criteria_met=False, quality_accepted=False)
        artifact['summary'] = result; legacy.save(args.artifact, artifact)
        args.artifact.with_suffix('.md').write_text('# Judgment comparison\n\n' + json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(result['status'])
        if not result['criteria_met']: raise SystemExit(1)
    else: print(len(audit(plan, json.loads(args.artifact.read_text()))), 'request observations; quality unreviewed')


if __name__ == '__main__':
    main()
