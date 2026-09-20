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
from copyeditor.judgment import POLICIES, THRESHOLDS, REFERENCES, definition_hash, _json_value
from copyeditor.judgment import JudgmentInput, JudgmentBlock, JudgmentFailure
from copyeditor.judgment_batch import prepare_judgments
from copyeditor.providers.base import Background
from copyeditor.providers.typesafe import TypeSafe
from copyeditor.metrics import Metrics
from copyeditor.judged_response import validate_judged_final
from copyeditor.responses import validate_final
from copyeditor.providers.base import SourceItem
from copyeditor.rules import load_rules
from examples_to_promptfoo import load_examples

RISK = dict(version='edit-risk-probe-v2', high_boundary=0.80, max_calls=300,
            input_budget=19200000, timeout_seconds=10,
            question='Would attempting the selected editing action be more likely to lose important meaning, nuance or appropriate register than to improve this text?')

COMPARISON = dict(acceptance=dict(problem=15, natural=5, per_example=4, per_repeat=12),
                  existing=dict(problem=18, natural=6, per_example=4, per_repeat=15), repeats=5, required_gain=1)

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
        conditions=conditions, acceptance_criteria=dict(legacy=legacy.THRESHOLDS, comparison=COMPARISON, quality_accepted=False, human_review='required'),
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
    if plan['sets'][0]['name'] == SETS['calibration']:
        artifact.update(calibrate(plan, artifact)); legacy.save(output, artifact)
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
            if response is None or response.get('schema_version') != (3 if request['judgment_enabled'] else 2 if request['degree'] == 'rewrite' else 1): raise ValueError()
            if any(response.get(k) != condition[k] for k in ('rules_version', 'common_version')): raise ValueError()
            if response.get('degree', 'polish') != request['degree'] or response.get('language') != 'ja': raise ValueError()
            if request['judgment_enabled']:
                if any(response[k] != condition[k] for k in ('policy_version', 'thresholds_version', 'policy_hash', 'thresholds_hash')): raise ValueError()
                if [r['model'] for r in response['providers']] != [condition['editing_model'], condition['judgment_model']]: raise ValueError()
                validate_judged_final(response, originals, format=cases[request['ids'][0]]['format'])
            else:
                if response.get('model') != condition['editing_model']: raise ValueError()
                if request['degree'] == 'rewrite': legacy.validate_rewrite_final(response, originals)
                else: validate_final(response)
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
            if legacy.preservation.check(case['bad'], text, protected, ratio, 'text' if case['format'] == 'html' else case['format']).failed or (case['format'] == 'html' and not legacy.html.same_structure(case['bad'], text)): failures.append('preservation')
        counts = dict(planned=1, problem=int(problem), natural=int(not problem), pending=int(pending),
            meaning_violations=int(decision['b'] is False), unnecessary_changes=int(decision['c'] is False),
            natural_changes=int(not problem and changed), natural_failures=int(not problem and (not healthy or changed or bool(block.get('flag')) or decision['d'] is False)),
            errors=int(bool(trial['error_code']) or response.get('status') != 'ok'), flags=int(bool(block.get('flag'))), integrity=len(failures),
            detection_misses=int(problem and block.get('detection', {}).get('status') == 'insufficient'),
            detection_false_positives=int(not problem and block.get('detection', {}).get('status') == 'eligible'),
            verification_failures=int(block.get('verification', {}).get('status') == 'fail'), verification_indeterminate=int(block.get('verification', {}).get('status') == 'indeterminate'))
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


def probability(value):
    if type(value) not in (float, int): return None
    value = Decimal(str(value))
    return value if value.is_finite() and 0 <= value <= 1 else None


def interval(low, high):
    if low is None or high is None: return dict(min=None, max=None, range=None, midpoint=None)
    # Decimal strings preserve a midpoint even between adjacent binary floats.
    with localcontext() as context:
        context.prec = max(28, -low.as_tuple().exponent, -high.as_tuple().exponent) + 3
        return dict(min=str(low), max=str(high), range=str(high - low), midpoint=str((low + high) / 2))


def calibrate(plan, artifact):
    if plan['sets'][0]['name'] != SETS['calibration']: raise ValueError('Calibration population required')
    reviewed = summarize(plan, artifact)
    invalid = {r['request_id'] for r in reviewed['judgments'] if 'response_integrity' in r['failures'] or 'preservation' in r['failures']}
    requests = {r['request_id']: r for r in plan['request_layouts']}
    kinds = {c['id']: c['kind'] for c in plan['sets'][0]['cases']}
    observations, risk = [], Counter(planned=0, measured=0, missing=0, high=0, high_verified=0, high_pass=0,
                                     not_generated=0, no_issue=0, preservation_rejected=0, production_error=0, not_eligible=0)
    missing_reasons = Counter()
    for trial in artifact['trials']:
        if not trial['judgment_enabled']: continue
        request, calibration = requests[trial['request_id']], trial['calibration'] or {}
        ordinal = request['ids'].index(trial['example_id']) + 1
        response = trial['full_response'] or {}
        block = next((b for b in response.get('items', [response]) if b.get('id', 'text') == ('text' if trial['layout'] == 'text' else f'b{ordinal:04}')), {})
        detection = block.get('detection', {})
        gate = probability(calibration.get('gate_probability'))
        action = detection.get('action') or {}
        coherent = gate is not None and gate == probability((detection.get('gate') or {}).get('probability')) and all(calibration.get(k) == action.get(v) for k, v in
            (('raw_action', 'selected'), ('effective_action', 'effective'), ('action_source', 'source'), ('confidence', 'confidence')))
        if not coherent or trial['error_code'] or trial['request_id'] in invalid: gate = None
        observations.append(dict(degree=trial['degree'], layout=trial['layout'], repeat=trial['repeat'], example_id=trial['example_id'],
            kind=kinds[trial['example_id']], gate=gate, eligible=detection.get('status') == 'eligible' if gate is not None else None))
        risk['planned'] += 1
        excluded = ('production_error' if trial['error_code'] else 'no_issue' if block.get('editing') == 'diagnosed_no_issue'
            else 'preservation_rejected' if (block.get('flag') or {}).get('kind') == 'rejected'
            else 'not_generated' if block.get('editing') != 'generated' else None)
        if excluded: risk[excluded] += 1
        if gate is not None and detection.get('status') != 'eligible' and calibration.get('risk_not_run_reason') == 'not_eligible' and all(calibration.get(k) is None for k in ('risk_probability', 'risk_error', 'risk_measurement')):
            risk['not_eligible'] += 1
            continue
        value = probability(calibration.get('risk_probability'))
        measurement = artifact.get('risk_measurements', {}).get(calibration.get('risk_measurement'), {})
        index = measurement.get('batch_index')
        batches = next((p['batches'] for p in trial['batch_plans'] if p['phase'] == 'detect'), [])
        linked = type(index) is int and 0 <= index < len(batches) and ordinal in batches[index]['ordinals'] and measurement.get('request_id') == trial['request_id'] and measurement.get('model_calls') == 1
        if gate is None or detection.get('status') != 'eligible' or value is None or calibration.get('risk_error') or calibration.get('risk_not_run_reason') or not linked:
            risk['missing'] += 1; missing_reasons[calibration.get('risk_not_run_reason') or 'invalid_or_missing_probe'] += 1
            continue
        risk['measured'] += 1
        high = value >= Decimal(str(plan['risk_probe']['high_boundary']))
        risk['high'] += high
        verification = block.get('verification', {}).get('status')
        if high and not excluded and verification in ('pass', 'fail', 'indeterminate'):
            risk['high_verified'] += 1; risk['high_pass'] += verification == 'pass'
    def group(values):
        natural = [v['gate'] for v in values if v['kind'] == 'natural' and v['gate'] is not None]
        unnatural = [v['gate'] for v in values if v['kind'] == 'problem' and v['gate'] is not None]
        span = interval(max(natural) if natural else None, min(unnatural) if unnatural else None)
        return dict(natural_max=span['min'], unnatural_min=span['max'], gap=span['range'], planned=len(values), observed=sum(v['gate'] is not None for v in values),
            false_positives=sum(v['kind'] == 'natural' and v['eligible'] is True for v in values), misses=sum(v['kind'] == 'problem' and v['eligible'] is False for v in values),
            complete=all(v['gate'] is not None for v in values))
    summary = group(observations)
    summary['by_degree_layout_repeat'], summary['per_case_variation'] = {}, {}
    for degree in ('polish', 'rewrite'):
        for layout in ('text', 'items'):
            values = [v for v in observations if v['degree'] == degree and v['layout'] == layout]
            for repeat in range(1, 6): summary['by_degree_layout_repeat'][f'{degree}/{layout}/{repeat}'] = group([v for v in values if v['repeat'] == repeat])
            for identity in kinds:
                repeated = sorted((v for v in values if v['example_id'] == identity), key=lambda v: v['repeat'])
                measured = [v['gate'] for v in repeated if v['gate'] is not None]
                variation = interval(min(measured), max(measured)) if measured else interval(None, None)
                variation.pop('midpoint')
                eligibility = [v['eligible'] for v in repeated]
                summary['per_case_variation'][f'{degree}/{layout}/{identity}'] = dict(variation, planned=5, observed=len(measured),
                    probabilities=[str(v['gate']) if v['gate'] is not None else None for v in repeated],
                    eligibility_flipped=len({v for v in eligibility if v is not None}) > 1,
                    transitions=sum(a is not None and b is not None and a != b for a, b in zip(eligibility, eligibility[1:])))
    summary['eligibility_flips'] = sum(v['eligibility_flipped'] for v in summary['per_case_variation'].values())
    risk = dict(risk, missing_reasons=dict(missing_reasons), complete=risk['missing'] == 0,
        high_pass_rate=risk['high_pass'] / risk['high_verified'] if risk['high_verified'] else None,
        recommendation='keep_disabled_false_veto' if risk['high_pass'] else 'unconfirmed' if risk['missing'] or not risk['high_verified'] else 'owner_review_required')
    condition = next(c for c in plan['conditions'] if c['judgment_enabled'])
    floor = (interval(Decimal(summary['natural_max']), Decimal(summary['unnatural_min']))['midpoint']
             if summary['complete'] and summary['gap'] is not None and Decimal(summary['gap']) > 0 else None)
    reason = ('incomplete_calibration' if not summary['complete'] else 'revise_gate_axes_actions_references_with_new_policy_and_remeasure' if floor is None
              else 'new_threshold_id_contract_hash_and_remeasurement_required' if Decimal(floor) != Decimal(str(THRESHOLDS[condition['thresholds_version']]['floor'])) else 'owner_review_and_unused_held_out_required')
    decision = dict(derived_floor=floor, thresholds_version=condition['thresholds_version'], policy_version=condition['policy_version'],
                    references_hash=plan['references_hash'], edit_risk=risk, reason=reason, reviewer=None)
    previous = artifact.get('calibration_decision') or {}
    if artifact.get('calibration_summary') == summary and {k: v for k, v in previous.items() if k != 'reviewer'} == {k: v for k, v in decision.items() if k != 'reviewer'} and isinstance(previous.get('reviewer'), str) and previous['reviewer'].strip(): decision['reviewer'] = previous['reviewer']
    return dict(calibration_summary=summary, calibration_decision=decision)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('plan', 'run', 'check', 'report', 'calibrate'))
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
    elif args.operation == 'calibrate':
        artifact = json.loads(args.artifact.read_text())
        try: result = calibrate(plan, artifact)
        except (ValueError, TypeError, KeyError): result = dict(calibration_summary=None, calibration_decision=None)
        artifact.update(result); legacy.save(args.artifact, artifact)
        args.artifact.with_suffix('.md').write_text('# Gate calibration\n\n' + json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        if result['calibration_decision'] is None or result['calibration_decision']['derived_floor'] is None: raise SystemExit(1)
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
