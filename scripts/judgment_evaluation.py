"""Freeze and run offline or explicitly opted-in live judgment comparisons; never accept quality."""
import argparse
import asyncio
from bisect import bisect_left
from collections import Counter
from contextlib import contextmanager
from unittest.mock import patch
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import json
from pathlib import Path
from time import monotonic
import benchmark_provider as adapter
import rewrite_evaluation as legacy
from copyeditor.judgment import definition_hash, _json_value, _probability
from copyeditor.judgment_v2 import POLICIES, REFERENCES, snapshot as judgment_snapshot
from copyeditor.edit_protocol import validate_final
from copyeditor.providers.base import SourceItem
from copyeditor.rules import load_rules
from examples_to_promptfoo import load_examples

COMPARISON = dict(acceptance=dict(problem=30, natural=10, per_example=4, per_repeat=24),
                  existing=dict(problem=18, natural=6, per_example=4, per_repeat=15), repeats=5, required_gain=1)

SETS = dict(calibration="calibration-v4", acceptance="judgment-acceptance-v4", existing="existing-rewrite-v4", regression="packing-regression-v4")

POPULATION_PINS = {'acceptance': ('judgment-v2-heldout', 40, '915867574207ba3970f5a8df9ee49ccb5b690182ecd14474abcb413872f90f19'), 'calibration': ('judgment-v2-calibration', 30, '65292c4234855ff9b55d3d3c7d9d6f7e0df687897838c693364b9bc99d00dfc5')}


def encoded(value):
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def population(name):
    name = next((k for k, v in SETS.items() if v == name), name)
    if name in POPULATION_PINS:
        prefix, count, expected = POPULATION_PINS[name]
        paths = sorted((legacy.ROOT / 'examples/ja').glob(prefix + '-*.yaml'))
        rows = [(p.relative_to(legacy.ROOT).as_posix(), legacy.digest(p.read_bytes())) for p in paths]
        if [p.stem for p in paths] != [f'{prefix}-{i:02}' for i in range(1, count + 1)] or legacy.digest(json.dumps(rows).encode()) != expected:
            raise ValueError('Missing or changed evaluation population')
    cases = {c['id']: c for c in load_examples(legacy.ROOT / 'examples', load_rules(legacy.ROOT / 'rules', None)) if c['language'] == 'ja'}
    prefix, count = {'calibration': ('judgment-v2-calibration', 30), 'acceptance': ('judgment-v2-heldout', 40), 'existing': ('rewrite', 24)}.get(name, ('', 0))
    if name == 'regression':
        return [dict(cases['judgment-calibration-01'], id=f'packing-{i}', bad=str(i) + 'あ' * 2390, good=str(i) + 'あ' * 2390, must_change=True) for i in range(1, 6)]
    return [cases[f'{prefix}-{i:02}'] for i in range(1, count + 1)]


def freeze(revision, mode='fixture', name='existing', created_at=None, pairs=None):
    name = next((k for k, v in SETS.items() if v == name), name)
    if type(revision) is not int or revision < 1 or mode not in ('fixture', 'live') or name not in ('calibration', 'acceptance', 'existing', 'regression'): raise ValueError('Invalid comparison plan')
    if name == 'calibration' and pairs is not None:
        from calibration_measurement import freeze as freeze_measurement
        return freeze_measurement(revision, mode, pairs, created_at)
    if name in ('calibration', 'acceptance') and mode == 'live' or pairs is not None:
        raise ValueError('Generation-four evaluation population and owner manifest are not complete')
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    cases = population(name)
    config, snapshot, _ = adapter.comparison_environment(cases[0], mode, False)
    selected_config, _, _ = adapter.comparison_environment(cases[0], mode, True)
    registry = adapter.fixture_registry if mode == 'fixture' else judgment_snapshot
    selected = registry(selected_config['judgment.policy_version'], selected_config['judgment.thresholds_version'])
    policy, threshold = selected.policy, selected.threshold
    entries = [dict(id=c['id'], path=None if name == 'regression' else f"examples/ja/{c['id']}.yaml",
                    sha256=legacy.digest(encoded(c).encode() if name == 'regression' else (legacy.ROOT / f"examples/ja/{c['id']}.yaml").read_bytes()), input_hash=legacy.digest(encoded(c).encode()), kind='problem' if c['must_change'] else 'natural',
                    origin='synthetic-v2:' + c['id'] if name in POPULATION_PINS else 'historical-regression:' + c['id']) for c in cases]
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
        created_at=created_at, sets=[dict(name=SETS[name], role='calibration' if name == 'calibration' else 'regression' if name == 'regression' else 'acceptance', cases=entries, repeats=5,
            population_hash=POPULATION_PINS[name][2] if name in POPULATION_PINS else None, owner_labels='pending', native_review='pending')],
        conditions=conditions, acceptance_criteria=dict(legacy=legacy.THRESHOLDS, comparison=COMPARISON, quality_accepted=False, human_review='required'),
        planned_trials=trials, request_layouts=requests, calibration_run_budget=dict(blocks=1200, requests=720) if name == 'calibration' else None,
        references_hash=definition_hash(REFERENCES), packing_version=policy['packing_version'],
        observation_version=2, config={k: v for k, v in config.items() if not k.startswith('auth.') and k != 'judgment.enabled'})))


@contextmanager
def observe_request(plans, trace):
    """Keep synthetic evaluation detail out of production responses and logs."""
    from copyeditor import edit_pipeline
    from copyeditor.providers.typesafe import TypeSafe
    evaluate, parse, verify = TypeSafe.evaluate, edit_pipeline.parse_generation, edit_pipeline.classify_verification
    check = edit_pipeline.preservation.check
    rounds, checking, verifying = Counter(), [], []
    async def judge(self, wire):
        payload = json.loads(wire)
        event = dict(kind='judgment', phase='verify' if 'originals' in payload['state'] else 'detect',
                     candidate_round=plans[-1]['candidate_round'], state=payload['state'], result=None, error=None)
        trace.append(event)
        try:
            result = await evaluate(self, wire)
            event['result'] = dict(model=getattr(result, 'model', None), usage=result.usage._asdict(),
                blocks=[dict(ordinal=b.ordinal, probabilities=dict(b.probabilities)) for b in getattr(result, 'blocks', ())])
            event['error'] = getattr(result, 'code', None)
            if event['phase'] == 'verify' and not event['error']:
                verifying.extend(b.ordinal for b in result.blocks)
            return result
        except BaseException as error:
            event['error'] = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'evaluation_error'
            raise
    def generation(result, items, *, stage):
        for item in items:
            rounds[item.id] += 1
        event = dict(kind='generation', degree=stage, rounds={i.id: rounds[i.id] for i in items},
                     candidates=None, usage=result.usage._asdict(), finish=result.finish, error=None)
        trace.append(event)
        try:
            candidates = parse(result, items, stage=stage)
            event['candidates'] = [dict(c._asdict(), flag=_json_value(c.flag)) for c in candidates]
            checking[:] = candidates
            return candidates
        except Exception as error:
            event['error'] = getattr(error, 'code', 'evaluation_error')
            raise
    def preservation(source, candidate, *args, **kwargs):
        result = check(source, candidate, *args, **kwargs)
        item = checking.pop(0)
        trace.append(dict(kind='preservation', id=item.id, candidate_round=rounds[item.id],
                          source=source, candidate=candidate, checks=list(result.failed),
                          retry_trigger=not item.flag and (bool(result.failed) or bool(plans) and source == candidate)))
        return result
    def verification(source, candidate, meaning, selected):
        accepted = verify(source, candidate, meaning, selected)
        trace.append(dict(kind='verification', ordinal=verifying.pop(0), source_gate=source, candidate_gate=candidate, meaning=meaning,
                          candidate_round=plans[-1]['candidate_round'], retry_trigger=not accepted))
        return accepted
    with patch.object(TypeSafe, 'evaluate', judge), patch.object(edit_pipeline, 'parse_generation', generation), \
            patch.object(edit_pipeline, 'classify_verification', verification), \
            patch.object(edit_pipeline.preservation, 'check', preservation):
        yield


def check_plan(plan):
    if encoded(plan) != encoded(freeze(plan['evaluation_revision'], plan['mode'], plan['sets'][0]['name'], plan['created_at'], (plan.get('verification') or {}).get('pairs'))): raise ValueError('Comparison inputs changed')


def audit(plan, artifact, *, allow_unexecuted=False):
    check_plan(plan)
    if artifact['manifest_bytes'] != encoded(plan) or artifact['manifest_hash'] != legacy.digest(encoded(plan).encode()): raise ValueError('Manifest replaced')
    keys = tuple(plan['planned_trials'][0])
    key = lambda t: encoded({k: t[k] for k in keys})
    if Counter(map(key, artifact['trials'])) != Counter(map(key, plan['planned_trials'])): raise ValueError('Missing, duplicate or moved trials')
    requests = {}
    for trial in artifact['trials']:
        if trial['full_response'] is None and not trial['error_code']: raise ValueError('Unmarked missing response')
        if trial['error_code'] == 'not_run':
            if not allow_unexecuted or any(trial[k] is not None for k in ('started_at', 'latency_ms', 'full_response', 'provider_measurements')) or trial['batch_plans'] or trial.get('trace'):
                raise ValueError('Unexecuted trial')
        elif trial['started_at'] is None or trial['latency_ms'] is None:
            raise ValueError('Unexecuted observation')
        if trial['full_response'] is not None and trial['provider_measurements'] != trial['full_response'].get('providers'):
            raise ValueError('Provider measurements replaced')
        observation = {k: trial[k] for k in ('full_response', 'error_code', 'provider_measurements', 'latency_ms', 'batch_plans', 'started_at')}
        observation['trace'] = trial.get('trace', [])
        previous = requests.setdefault(trial['request_id'], observation)
        if previous != observation: raise ValueError('Inconsistent request observation')
    for request in plan['request_layouts']:
        check_trace(request, requests[request['request_id']])
    return requests  # Aggregate calls and costs once per request, never once per block.


def check_trace(request, observation):
    trace = observation['trace']
    if type(trace) is not list or any(type(e) is not dict or e.get('kind') not in
            ('generation', 'judgment', 'verification', 'preservation') for e in trace):
        raise ValueError('Invalid evaluation trace')
    phases = [(p['phase'], p['candidate_round']) for p in observation['batch_plans']]
    if phases != ([('detect', 0), ('verify', 1), ('verify', 2)][:len(phases)] if request['judgment_enabled'] else []):
        raise ValueError('Invalid phase sequence')
    identities = {'text'} if request['layout'] == 'text' else {f'b{i:04}' for i in range(1, len(request['ids']) + 1)}
    planned = [(p['phase'], p['candidate_round']) for p in observation['batch_plans'] for _ in p['batches']]
    judges = [e for e in trace if e['kind'] == 'judgment']
    if [(e['phase'], e['candidate_round']) for e in judges] != planned[:len(judges)]:
        raise ValueError('Judgment round trace changed')
    response = observation['full_response'] or {}
    calls = response.get('providers')
    if response.get('status') == 'ok' and isinstance(calls, list) and len(calls) == (2 if request['judgment_enabled'] else 1):
        if len(judges) != (calls[1]['model_calls'] if request['judgment_enabled'] else 0) or len(judges) != len(planned):
            raise ValueError('Missing judgment observations')
        if sum(e['kind'] == 'generation' for e in trace) != calls[0]['model_calls']:
            raise ValueError('Missing generation observations')
    seen = Counter()
    candidates, originals, gates, preserved, expected_verification = {}, {}, {}, set(), {}
    pending_preservation = []
    for event in trace:
        if event['kind'] == 'generation':
            if pending_preservation or expected_verification:
                raise ValueError('Incomplete previous round')
            if not event['rounds'] or not set(event['rounds']) <= identities:
                raise ValueError('Generation identity trace changed')
            for identity, round_ in event['rounds'].items():
                seen[identity] += 1
                if type(round_) is not int or round_ != seen[identity] or round_ > 2:
                    raise ValueError('Generation round trace changed')
            if event['candidates'] is not None:
                if [c['id'] for c in event['candidates']] != list(event['rounds']):
                    raise ValueError('Candidate identity trace changed')
                for candidate in event['candidates']:
                    identity = candidate['id']
                    candidates[identity] = candidate
                    preserved.discard(identity)
                    pending_preservation.append(identity)
        elif event['kind'] == 'preservation':
            identity = event['id']
            if not pending_preservation or pending_preservation.pop(0) != identity:
                raise ValueError('Preservation order changed')
            candidate = candidates[identity]
            if event['candidate_round'] != seen[identity] or event['candidate'] != candidate['text']:
                raise ValueError('Preservation candidate changed')
            if originals.setdefault(identity, event['source']) != event['source']:
                raise ValueError('Preservation source changed')
            trigger = not candidate['flag'] and (bool(event['checks']) or request['judgment_enabled'] and event['source'] == event['candidate'])
            if event['retry_trigger'] != trigger:
                raise ValueError('Preservation retry changed')
            preserved.add(identity)
        elif event['kind'] in ('judgment', 'verification'):
            if type(event['candidate_round']) is not int or event['candidate_round'] not in (0, 1, 2):
                raise ValueError('Invalid candidate round')
            if event['kind'] == 'verification':
                expected = expected_verification.pop(event['ordinal'], None)
                if expected is None or expected != (event['candidate_round'], event['source_gate'], event['candidate_gate'], event['meaning']):
                    raise ValueError('Verification observation changed')
                for field in ('source_gate', 'candidate_gate', 'meaning'):
                    _probability(event[field])
            else:
                if pending_preservation:
                    raise ValueError('Judgment precedes round observations')
                for key, value in event['state']['texts'].items():
                    identity = 'text' if request['layout'] == 'text' else key
                    if identity not in identities:
                        raise ValueError('Judgment identity changed')
                    if event['phase'] == 'detect':
                        if seen or originals.setdefault(identity, value['text']) != value['text']:
                            raise ValueError('Detection source changed')
                    elif identity not in preserved or seen[identity] != event['candidate_round'] or candidates[identity]['text'] != value['text'] or originals[identity] != event['state']['originals'][key]:
                        raise ValueError('Judgment candidate changed')
                if event['result'] is None or event['error']:
                    continue
                blocks = event['result']['blocks']
                if [b['ordinal'] for b in blocks] != [int(k[1:]) for k in event['state']['texts']]:
                    raise ValueError('Judgment block trace changed')
                for block in blocks:
                    expected = {'gate'} if event['phase'] == 'detect' else {'gate', 'meaning'}
                    if set(block['probabilities']) != expected:
                        raise ValueError('Judgment probability trace changed')
                    for value in block['probabilities'].values():
                        _probability(value)
                    ordinal, values = block['ordinal'], block['probabilities']
                    if event['phase'] == 'detect':
                        gates[ordinal] = values['gate']
                    else:
                        expected_verification[ordinal] = (event['candidate_round'], gates[ordinal], values['gate'], values['meaning'])
    if response.get('status') == 'ok':
        if pending_preservation or expected_verification:
            raise ValueError('Missing round observations')


async def run(plan, output, runner=adapter.compare_request):
    check_plan(plan)
    artifact = dict(manifest_bytes=encoded(plan), manifest_hash=legacy.digest(encoded(plan).encode()), trials=[dict(t,
        started_at=None, full_response=None, error_code='not_run', provider_measurements=None, latency_ms=None, batch_plans=[], trace=[],
        decision=dict.fromkeys('abcd'), reasons=dict.fromkeys('abcd'), reviewer=None) for t in plan['planned_trials']])
    legacy.save(output, artifact)
    cases = {c['id']: c for c in population(plan['sets'][0]['name'])}
    for request in plan['request_layouts']:
        plans, trace, response, error, cancelled = [], [], None, None, False
        started, clock = datetime.now(timezone.utc).isoformat(), monotonic()
        try:
            with observe_request(plans, trace):
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
                             latency_ms=elapsed, batch_plans=json.loads(encoded(plans)), trace=json.loads(encoded(trace)))
        legacy.save(output, artifact)
        if cancelled: raise asyncio.CancelledError
    audit(plan, artifact)
    return artifact


def summarize(plan, artifact):
    try:
        observations = audit(plan, artifact, allow_unexecuted=True)
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
        counts = dict(planned=1, not_run=int(trial['error_code'] == 'not_run'), missing_response=int(trial['full_response'] is None), problem=int(problem), natural=int(not problem), pending=int(pending),
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
        group.update(requests=len(measured), not_run_requests=sum(r['error_code'] == 'not_run' for r in measured),
            latency_ms=sum(r['latency_ms'] for r in measured) if all(r['latency_ms'] is not None for r in measured) else None,
            candidate_rounds=dict(Counter(str(p['candidate_round']) for r in measured for p in r['batch_plans'] if 'candidate_round' in p)))
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


PAIR_KINDS = frozenset(('improved', 'same', 'unimproved', 'unrelated', 'meaning', 'negation', 'condition', 'promise'))


def calibrate(plan, artifact, pair_plan, pair_artifact):
    """Derive offline candidates from complete observations and pre-labelled pairs."""
    observations = audit(plan, artifact)
    if plan['sets'][0]['name'] != SETS['calibration']:
        raise ValueError('A complete calibration population and owner manifest are required')
    source_hash = legacy.digest(encoded(plan).encode())
    cases = {c['id']: c for c in population('calibration')}
    if type(pair_plan) is not dict or type(pair_artifact) is not dict:
        raise ValueError('Invalid candidate pair ledger')
    labels = pair_plan.get('labels', {})
    if (pair_plan.get('source_manifest_hash') != source_hash or
            not isinstance(pair_plan.get('owner'), str) or not pair_plan['owner'].strip() or
            type(labels) is not dict or set(labels) != set(cases)):
        raise ValueError('Missing or changed owner labels')
    for identity, case in cases.items():
        label = labels[identity]
        if (type(label) is not dict or label.get('kind') != ('problem' if case['must_change'] else 'natural') or
                not isinstance(label.get('reason'), str) or not label['reason'].strip()):
            raise ValueError('Missing or changed owner labels')
    pairs = pair_plan.get('pairs', [])
    if (type(pairs) is not list or not pairs or
            any(type(p) is not dict or not isinstance(p.get('id'), str) or not p['id'].strip() for p in pairs) or
            len({p['id'] for p in pairs}) != len(pairs)):
        raise ValueError('Missing or duplicate candidate pairs')
    by_id, coverage = {}, {identity: set() for identity in cases}
    for pair in pairs:
        identity, kind = pair['source_id'], pair['kind']
        if (not isinstance(identity, str) or identity not in cases or not isinstance(kind, str) or kind not in PAIR_KINDS or
                type(pair.get('accepted')) is not bool or pair['accepted'] != (kind == 'improved') or
                not isinstance(pair.get('reason'), str) or not pair['reason'].strip() or
                not isinstance(pair.get('candidate'), str) or not pair['candidate'].strip() or
                (pair['candidate'] == cases[identity]['bad']) != (kind == 'same')):
            raise ValueError('Missing or inconsistent prior pair judgment')
        by_id[pair['id']] = pair
        coverage[identity].add(kind)
    if any(kinds != PAIR_KINDS for kinds in coverage.values()):
        raise ValueError('Missing candidate controls')
    pair_bytes = encoded(pair_plan)
    if pair_artifact.get('manifest_bytes') != pair_bytes or pair_artifact.get('manifest_hash') != legacy.digest(pair_bytes.encode()):
        raise ValueError('Pair manifest replaced')
    expected_pairs = Counter((p['id'], repeat) for p in pairs for repeat in range(1, 6))
    trials = pair_artifact.get('trials', [])
    if (type(trials) is not list or any(type(t) is not dict or
            not isinstance(t.get('pair_id'), str) or type(t.get('repeat')) is not int for t in trials) or
            Counter((t['pair_id'], t['repeat']) for t in trials) != expected_pairs):
        raise ValueError('Missing, duplicate or moved pair trials')
    limit = pair_plan.get('search_limit')
    if type(limit) is not int or limit < 1:
        raise ValueError('Missing finite search budget')
    detected, measured = [], []
    for request in plan['request_layouts']:
        observation = observations[request['request_id']]
        if observation['error_code'] or not observation['full_response'] or observation['full_response'].get('status') != 'ok':
            raise ValueError('Incomplete calibration observations')
        for event in observation['trace']:
            if event['kind'] != 'judgment' or event['phase'] != 'detect': continue
            if event['error'] or event['result'] is None:
                raise ValueError('Incomplete detection observations')
            for block in event['result']['blocks']:
                identity = request['ids'][block['ordinal'] - 1]
                detected.append((identity, request['degree'], request['layout'], request['repeat']))
                measured.append((labels[identity]['kind'], Decimal(str(_probability(block['probabilities']['gate'])))))
    expected = Counter((t['example_id'], t['degree'], t['layout'], t['repeat'])
                       for t in plan['planned_trials'] if t['judgment_enabled'])
    if Counter(detected) != expected:
        raise ValueError('Missing detection trials')
    pair_values = []
    for trial in trials:
        if 'error' not in trial or trial['error'] is not None:
            raise ValueError('Failed pair observation')
        values = tuple(Decimal(str(_probability(trial[key]))) for key in ('source_gate', 'candidate_gate', 'meaning'))
        pair_values.append((by_id[trial['pair_id']]['accepted'], *values))
    return select_thresholds(measured, pair_values, source_hash, legacy.digest(pair_bytes.encode()), limit)


def select_thresholds(measured, pair_values, source_hash, pair_hash, limit):
    """Share the exact finite search between legacy fixtures and raw measurements."""
    values = [v for _, v in measured] + [v for row in pair_values for v in row[1:]]
    # Work in decimal input precision; converting candidates to float can collapse a midpoint.
    with localcontext() as context:
        context.prec = max(32, max(-v.as_tuple().exponent for v in values) + 4)
        natural = max(v for label, v in measured if label == 'natural')
        problem = min(v for label, v in measured if label == 'problem')
        gap = problem - natural
        result = dict(status='not_separated', N=format(natural, 'f'), U=format(problem, 'f'), G=format(gap, 'f'),
            thresholds=None, quality_accepted=False, production_registered=False,
            source_manifest_hash=source_hash, pair_manifest_hash=pair_hash)
        if gap <= 0: return result
        floor = (natural + problem) / 2
        scores = [(accepted, source - candidate, meaning) for accepted, source, candidate, meaning in pair_values]
        def candidates(values):
            ordered = sorted(set(values))
            return sorted({Decimal(0), Decimal(1), *ordered,
                           *((left + right) / 2 for left, right in zip(ordered, ordered[1:]))})
        gaps = [v for v in candidates(delta for _, delta, _ in scores if delta > 0) if v > 0]
        meanings = candidates(meaning for _, _, meaning in scores)
        combinations = len(gaps) * len(meanings)
        result.update(search_candidates=combinations, search_limit=limit)
        if combinations > limit:
            result['status'] = 'search_budget_exceeded'
            return result
        best = None
        for candidate_gap in gaps:
            positives = sorted(meaning for accepted, delta, meaning in scores if accepted and delta >= candidate_gap)
            forbidden = max((meaning for accepted, delta, meaning in scores if not accepted and delta >= candidate_gap), default=Decimal(-1))
            for meaning_floor in meanings:
                if meaning_floor <= forbidden: continue
                count = len(positives) - bisect_left(positives, meaning_floor)
                if count and (best is None or (count, candidate_gap, meaning_floor) > best):
                    best = count, candidate_gap, meaning_floor
        if best is None:
            result['status'] = 'no_feasible_thresholds'
            return result
        count, selected_gap, meaning_floor = best
        result.update(status='candidate', accepted_improvement_trials=count,
            thresholds={k: format(v, 'f') for k, v in dict(floor=floor, gap=selected_gap, meaning_floor=meaning_floor).items()})
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('plan', 'run', 'check', 'report', 'calibrate', 'verify', 'verify-report'))
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--artifact', type=Path, default=Path('judgment-evaluation.json'))
    parser.add_argument('--revision', type=int, default=1)
    parser.add_argument('--mode', choices=('fixture', 'live'), default='fixture')
    parser.add_argument('--set', dest='name', choices=('calibration', 'acceptance', 'existing', 'regression'), default='existing')
    parser.add_argument('--pairs', type=Path)
    parser.add_argument('--approval', type=Path)
    parser.add_argument('--thresholds', type=Path)
    args = parser.parse_args()
    if args.operation == 'plan': return legacy.save(args.plan, freeze(args.revision, args.mode, args.name, pairs=json.loads(args.pairs.read_text()) if args.pairs else None))
    plan = json.loads(args.plan.read_text())
    if args.mode != plan['mode']: parser.error('Mode must match frozen plan; live requires --mode live')
    if plan.get('schema') == 'copyeditor-calibration-measurement-v1':
        from calibration_measurement import command
        try:
            return command(args, plan)
        except (ValueError, KeyError, TypeError, OSError):
            parser.error('Calibration measurement refused; check inputs, approval and artifact status')
    if args.mode == 'live':
        parser.error('Live execution requires an approved calibration measurement plan')
    if args.operation == 'run': asyncio.run(run(plan, args.artifact))
    elif args.operation == 'calibrate':
        try:
            if args.pairs is None:
                raise ValueError('Generation-four calibration requires a complete population and owner manifest')
            bundle = json.loads(args.pairs.read_text())
            artifact = json.loads(args.artifact.read_text())
            result = calibrate(plan, artifact, bundle['manifest'], bundle['observations'])
        except (ValueError, KeyError, TypeError) as error:
            parser.error(str(error))
        artifact['calibration'] = result
        legacy.save(args.artifact, artifact)
        print(result['status'])
        if result['status'] != 'candidate': raise SystemExit(1)
    elif args.operation in ('verify', 'verify-report'):
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
