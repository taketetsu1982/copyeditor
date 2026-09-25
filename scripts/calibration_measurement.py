"""Owner-approved raw calibration, isolated from production registries and services."""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import json
import os
from pathlib import Path
from time import monotonic
from unittest.mock import patch

import judgment_evaluation as evaluation
from copyeditor.judgment import JudgmentBlock, JudgmentBlockResult, JudgmentInput, JudgmentResult, definition_hash
from copyeditor.judgment_v2 import POLICY_ID, POLICY_HASH, REFERENCES
from copyeditor.judgment_v2_batch import prepare_judgments
from copyeditor.providers.base import Background, Usage

SCHEMA = 'copyeditor-calibration-measurement-v1'
SCOPE = 'synthetic-calibration-only; TypeSafe raw judgments; no Vertex; no registration'
OUTPUT_RESERVATION = 65536


def require(condition, message='Invalid calibration measurement input'):
    if not condition:
        raise ValueError(message)


def number(value, *, positive=False):
    require(type(value) in (str, int, float, Decimal))
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Invalid measurement number') from None
    require(result.is_finite() and (result > 0 if positive else result >= 0))
    return result


def fingerprint(value):
    return evaluation.legacy.digest(evaluation.encoded(value).encode())


def inputs(bundle):
    """Validate all labels and controls before loading a secret or provider."""
    require(type(bundle) is dict and set(bundle) == {'owner', 'labels', 'pairs', 'search_limit',
                                                   'editing_model', 'tone', 'budget'})
    require(all(type(bundle[k]) is str and bundle[k].strip() for k in ('owner', 'editing_model', 'tone')))
    require(bundle['editing_model'] == 'gemini-3.1-flash-lite')
    require(type(bundle['search_limit']) is int and bundle['search_limit'] > 0)
    cases = {c['id']: c for c in evaluation.population('calibration')}
    require(type(bundle['labels']) is dict and set(bundle['labels']) == set(cases))
    for identity, case in cases.items():
        label = bundle['labels'][identity]
        require(type(label) is dict and set(label) == {'kind', 'reason'})
        require(label['kind'] == ('problem' if case['must_change'] else 'natural'))
        require(type(label['reason']) is str and bool(label['reason'].strip()))
    require(type(bundle['pairs']) is list and len(bundle['pairs']) == len(cases) * 8)
    seen = set()
    for pair in bundle['pairs']:
        require(type(pair) is dict and set(pair) == {'id', 'source_id', 'kind', 'candidate', 'accepted', 'reason'})
        identity, kind = pair['source_id'], pair['kind']
        require(type(identity) is str and identity in cases and type(kind) is str and kind in evaluation.PAIR_KINDS)
        require(pair['id'] == identity + '/' + kind and pair['id'] not in seen)
        seen.add(pair['id'])
        require(type(pair['accepted']) is bool and pair['accepted'] == (kind == 'improved'))
        require(type(pair['reason']) is str and bool(pair['reason'].strip()))
        candidate = pair['candidate']
        require(type(candidate) is str and bool(candidate.strip()) and len(candidate) <= 16000)
        require((candidate == cases[identity]['bad']) == (kind == 'same'))
    require(seen == {identity + '/' + kind for identity in cases for kind in evaluation.PAIR_KINDS})
    budget = bundle['budget']
    require(type(budget) is dict and set(budget) == {'max_calls', 'max_input_units', 'max_output_tokens',
        'max_seconds', 'max_cost', 'currency', 'input_per_million', 'output_per_million'})
    for key in ('max_calls', 'max_input_units', 'max_output_tokens', 'max_seconds'):
        require(type(budget[key]) is int and budget[key] > 0)
    require(type(budget['currency']) is str and len(budget['currency']) == 3 and budget['currency'].isascii()
            and budget['currency'].isupper() and budget['currency'].isalpha())
    for key in ('input_per_million', 'output_per_million', 'max_cost'):
        number(budget[key], positive=key == 'max_cost')
    return cases


def schedule(cases, bundle):
    """Singleton requests keep packing regressions out of calibration denominators."""
    for condition, tone in (('background-none', ''), ('explicit-tone', bundle['tone'])):
        for repeat in range(1, 6):
            for degree in ('polish', 'rewrite'):
                for identity in cases:
                    yield dict(id=f'{condition}/{repeat}/{degree}/{identity}', source_id=identity,
                               condition=condition, tone=tone, repeat=repeat, degree=degree, pair_id=None)
            for pair in bundle['pairs']:
                yield dict(id=f"{condition}/{repeat}/{pair['id']}", source_id=pair['source_id'],
                           condition=condition, tone=tone, repeat=repeat, degree=None, pair_id=pair['id'])


def prepare(row, cases, pairs):
    case = cases[row['source_id']]
    background = Background('', '', row['tone'], '')
    candidate = pairs[row['pair_id']]['candidate'] if row['pair_id'] else None
    block = JudgmentBlock(1, case['bad'], '', candidate, None)
    phases = ('detect', 'verify') if candidate is not None else ('detect',)
    requests = []
    for phase in phases:
        data = JudgmentInput(phase, 'ja', case['format'], background, row['tone'], (block,))
        planned = prepare_judgments(data, candidate_round=int(phase == 'verify'))
        require(len(planned.requests) == 1)
        requests.append((phase, planned.requests[0], planned.plan.batches[0].input_units))
    return requests


def freeze(revision, mode, bundle, created_at=None):
    require(type(revision) is int and revision > 0 and mode in ('fixture', 'live'))
    cases = inputs(bundle)
    pairs = {p['id']: p for p in bundle['pairs']}
    rows = list(schedule(cases, bundle))
    calls = units = 0
    for row in rows:
        for _, wire, reserved in prepare(row, cases, pairs):
            calls += 1
            units += reserved
    rules = evaluation.load_rules(evaluation.legacy.ROOT / 'rules', None)
    paths = ['src/copyeditor/prompt.py', 'src/copyeditor/judgment_v2.py', 'src/copyeditor/judgment_v2_batch.py',
             'src/copyeditor/providers/typesafe.py', 'scripts/calibration_measurement.py',
             'scripts/judgment_evaluation.py', 'rules/common.md', 'rules/ja.md']
    pins = dict(policy_id=POLICY_ID, policy_hash=POLICY_HASH, references_hash=definition_hash(REFERENCES),
                judgment_model='jev-1.13.0', editing_model=bundle['editing_model'], thinking='low',
                rules_version=rules.rules_version, common_version=rules.common_version,
                packing_version='request-pack-v2', request_timeout_ms=10000,
                files={p: evaluation.legacy.digest((evaluation.legacy.ROOT / p).read_bytes()) for p in paths})
    population = [{'id': c['id'], 'path': f"examples/ja/{c['id']}.yaml", 'origin': 'synthetic-v2:' + c['id'],
                   'sha256': evaluation.legacy.digest((evaluation.legacy.ROOT / f"examples/ja/{c['id']}.yaml").read_bytes())}
                  for c in cases.values()]
    output = calls * OUTPUT_RESERVATION
    budget = bundle['budget']
    cost = (Fraction(units) * Fraction(number(budget['input_per_million'])) +
            Fraction(output) * Fraction(number(budget['output_per_million']))) / 1000000
    require(calls <= budget['max_calls'] and units <= budget['max_input_units'] and output <= budget['max_output_tokens']
            and cost <= Fraction(number(budget['max_cost'])), 'Measurement exceeds approved reservation budget')
    return json.loads(evaluation.encoded(dict(schema=SCHEMA, evaluation_revision=revision, mode=mode,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
        source_commit=evaluation.legacy.subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=evaluation.legacy.ROOT, text=True).strip(),
        scope=SCOPE, pins=pins, population=population, inputs=bundle, planned_trials=rows,
        reservations=dict(calls=calls, input_units=units, output_tokens=output,
                          cost_fraction=[cost.numerator, cost.denominator], currency=budget['currency']),
        quality_accepted=False, production_registered=False)))


def check_plan(plan):
    require(type(plan) is dict and plan.get('schema') == SCHEMA)
    expected = freeze(plan['evaluation_revision'], plan['mode'], plan['inputs'], plan['created_at'])
    require(evaluation.encoded(plan) == evaluation.encoded(expected), 'Measurement inputs or source revision changed')


def check_approval(plan, approval):
    require(type(approval) is dict and set(approval) == {'owner', 'manifest_hash', 'scope', 'mode',
                                                      'approved', 'approved_at', 'expires_at', 'artifact_path'})
    require(approval['approved'] is True and approval['owner'] == plan['inputs']['owner']
            and approval['manifest_hash'] == fingerprint(plan) and approval['scope'] == SCOPE
            and approval['mode'] == plan['mode'], 'Missing or mismatched owner approval')
    start, end = (datetime.fromisoformat(approval[k]) for k in ('approved_at', 'expires_at'))
    require(start.tzinfo is not None and end.tzinfo is not None and start < end)
    require(type(approval['artifact_path']) is str and Path(approval['artifact_path']).is_absolute())
    return start, end


def authorize(plan, approval, output):
    check_plan(plan)
    start, end = check_approval(plan, approval)
    require(start <= datetime.now(timezone.utc) < end, 'Expired or future approval')
    require(Path(approval['artifact_path']).resolve() == Path(output).resolve(), 'Unapproved artifact path')
    return end.timestamp()


class FixtureJudgment:
    """Synthetic scores only; never evidence for registered values or quality."""
    def __init__(self, plan):
        cases = inputs(plan['inputs'])
        self.sources = {c['bad']: .9 if c['must_change'] else .2 for c in cases.values()}
        self.candidates = {p['candidate']: p['kind'] for p in plan['inputs']['pairs']}

    async def evaluate(self, wire, *, remaining_seconds):
        state = json.loads(wire)['state']
        text = state['texts']['b0001']['text']
        probabilities = [('gate', self.sources[text])] if 'originals' not in state else [
            ('gate', self.sources.get(text, .1)),
            ('meaning', .95 if self.candidates.get(text) == 'improved' else .1)]
        return JudgmentResult('jev-1.13.0', (JudgmentBlockResult(1, tuple(probabilities), None),), Usage(1, 1, 2))

    async def aclose(self):
        pass


def live_client():
    from copyeditor.judgment_config import JudgmentSecret
    from copyeditor.providers.typesafe import TypeSafe
    key = os.environ.get('TYPESAFE_API_KEY')
    require(type(key) is str and 0 < len(key) <= 4096 and all('!' <= c <= '~' for c in key),
            'Runtime judgment secret unavailable')
    return TypeSafe(JudgmentSecret(key), timeout_ms=10000)


async def run(plan, output, approval, *, client_factory=None, clock=monotonic):
    expiry = authorize(plan, approval, output)
    output = Path(output)
    require(not output.exists(), 'Use a new artifact path; measured calls must never be replayed')
    cases = inputs(plan['inputs'])
    pairs = {p['id']: p for p in plan['inputs']['pairs']}
    budget = plan['inputs']['budget']
    deadline = clock() + budget['max_seconds']
    artifact = dict(schema=SCHEMA, manifest_bytes=evaluation.encoded(plan), manifest_hash=fingerprint(plan),
                    approval=approval, status='not_run', trials=[], error=None,
                    quality_accepted=False, production_registered=False)
    # Exclusive creation prevents two processes from spending the same approval/artifact concurrently.
    with output.open('x') as stream:
        stream.write(evaluation.encoded(artifact))
    used_calls = used_input = 0
    client = None
    with ExitStack() as stack:
        if plan['mode'] == 'fixture':
            stack.enter_context(patch('socket.socket', side_effect=RuntimeError('Fixture network denied')))
            stack.enter_context(patch('socket.getaddrinfo', side_effect=RuntimeError('Fixture network denied')))
        try:
            require(clock() < deadline and datetime.now(timezone.utc).timestamp() < expiry, 'Measurement deadline')
            client = client_factory() if client_factory else FixtureJudgment(plan) if plan['mode'] == 'fixture' else live_client()
            for row in plan['planned_trials']:
                trial = dict(id=row['id'], calls=[], error=None)
                artifact['trials'].append(trial)
                for phase, wire, reserved in prepare(row, cases, pairs):
                    remaining = min(deadline - clock(), expiry - datetime.now(timezone.utc).timestamp(), 10)
                    require(remaining > 0 and used_calls < budget['max_calls']
                            and used_input + reserved <= budget['max_input_units'], 'Measurement budget exhausted')
                    used_calls += 1
                    used_input += reserved
                    call = dict(phase=phase, wire_hash=evaluation.legacy.digest(wire), input_units=reserved,
                                started_at=datetime.now(timezone.utc).isoformat(), latency_ms=None, result=None,
                                usage=None, error='interrupted')
                    trial['calls'].append(call)
                    artifact['status'] = 'running'
                    evaluation.legacy.save(output, artifact)
                    start = clock()
                    async with asyncio.timeout(remaining):
                        response = await client.evaluate(wire, remaining_seconds=remaining)
                    call['latency_ms'] = round((clock() - start) * 1000)
                    call['usage'] = response.usage._asdict()
                    call['error'] = getattr(response, 'code', 'invalid_response')
                    require(isinstance(response, JudgmentResult) and response.model == 'jev-1.13.0'
                            and len(response.blocks) == 1 and response.blocks[0].ordinal == 1,
                            'Invalid measurement response')
                    scores = dict(response.blocks[0].probabilities)
                    require(set(scores) == ({'gate'} if phase == 'detect' else {'gate', 'meaning'}))
                    for value in scores.values():
                        evaluation._probability(value)
                    call['result'], call['error'] = scores, None
                    for value, limit in ((response.usage.input_tokens, reserved),
                                         (response.usage.output_tokens, OUTPUT_RESERVATION)):
                        require(type(value) is int and 0 <= value <= limit, 'Missing usage or reservation overrun')
                    require(clock() < deadline and datetime.now(timezone.utc).timestamp() < expiry, 'Measurement deadline')
                    evaluation.legacy.save(output, artifact)
            artifact['status'] = 'complete'
        except (Exception, asyncio.CancelledError):
            artifact['status'], artifact['error'] = 'stopped', 'measurement_failed_or_budget_exhausted'
            # Preserve the started slot even for timeout/cancellation; do not start another call.
            raise ValueError('Measurement stopped; inspect the synthetic artifact') from None
        finally:
            evaluation.legacy.save(output, artifact)
            if client:
                await client.aclose()
    return artifact


def audit(plan, artifact):
    check_plan(plan)
    approved_at, expires_at = check_approval(plan, artifact['approval'])
    require(artifact.get('manifest_bytes') == evaluation.encoded(plan) and artifact.get('manifest_hash') == fingerprint(plan))
    require(artifact.get('status') == 'complete' and artifact.get('error') is None, 'Incomplete raw observations')
    require([t['id'] for t in artifact['trials']] == [r['id'] for r in plan['planned_trials']], 'Missing or moved trials')
    cases = inputs(plan['inputs'])
    pairs = {p['id']: p for p in plan['inputs']['pairs']}
    measured, pair_values = [], []
    for row, trial in zip(plan['planned_trials'], artifact['trials']):
        expected = prepare(row, cases, pairs)
        require(trial['error'] is None and len(trial['calls']) == len(expected))
        for call, (phase, wire, reserved) in zip(trial['calls'], expected):
            require(call['phase'] == phase and call['wire_hash'] == evaluation.legacy.digest(wire)
                    and call['input_units'] == reserved and call['error'] is None and call['started_at']
                    and type(call['latency_ms']) is int and call['latency_ms'] >= 0)
            started = datetime.fromisoformat(call['started_at'])
            require(started.tzinfo is not None and approved_at <= started < expires_at)
            require(type(call['result']) is dict and set(call['result']) == ({'gate'} if phase == 'detect' else {'gate', 'meaning'}))
            for value in call['result'].values():
                evaluation._probability(value)
            for field, limit in (('input_tokens', reserved), ('output_tokens', OUTPUT_RESERVATION)):
                require(type(call['usage'][field]) is int and 0 <= call['usage'][field] <= limit)
        source = Decimal(str(trial['calls'][0]['result']['gate']))
        if row['pair_id']:
            values = trial['calls'][1]['result']
            pair_values.append((pairs[row['pair_id']]['accepted'], source,
                                Decimal(str(values['gate'])), Decimal(str(values['meaning']))))
        else:
            measured.append((plan['inputs']['labels'][row['source_id']]['kind'], source))
    return measured, pair_values


def calibrate(plan, artifact):
    measured, pairs = audit(plan, artifact)
    result = evaluation.select_thresholds(measured, pairs, fingerprint(plan), fingerprint(plan['inputs']['pairs']),
                                         plan['inputs']['search_limit'])
    result['mode'] = plan['mode']
    return result


def verify(plan, artifact, thresholds):
    """Evaluate candidate numbers only against raw data, never via a runtime registry."""
    measured, pairs = audit(plan, artifact)
    require(type(thresholds) is dict and set(thresholds) == {'floor', 'gap', 'meaning_floor'})
    floor, gap, meaning = (Fraction(number(thresholds[k], positive=k == 'gap')) for k in ('floor', 'gap', 'meaning_floor'))
    require(max(floor, gap, meaning) <= 1)
    detection_errors = sum((Fraction(value) >= floor) != (kind == 'problem') for kind, value in measured)
    outcomes = [(accepted, Fraction(source) - Fraction(candidate) >= gap and Fraction(score) >= meaning)
                for accepted, source, candidate, score in pairs]
    unsafe = sum(not expected and actual for expected, actual in outcomes)
    improvements = sum(expected and actual for expected, actual in outcomes)
    return dict(status='candidate_evaluated', thresholds={k: str(v) for k, v in thresholds.items()},
                manifest_hash=fingerprint(plan), mode=plan['mode'], detection_errors=detection_errors,
                unsafe_controls=unsafe, accepted_improvement_trials=improvements,
                feasible=detection_errors == 0 and unsafe == 0 and improvements > 0,
                quality_accepted=False, production_registered=False,
                comparison='raw candidate-pair evaluation, not end-to-end OFF/ON quality acceptance')


def command(args, plan):
    if args.operation == 'run':
        require(args.approval is not None, 'Owner approval is required')
        asyncio.run(run(plan, args.artifact, json.loads(args.approval.read_text())))
        print('complete; quality unaccepted; production unregistered')
        return
    artifact = json.loads(args.artifact.read_text())
    if args.operation == 'calibrate':
        report = calibrate(plan, artifact)
        artifact['calibration'] = report
        evaluation.legacy.save(args.artifact, artifact)
        print(report['status'])
        if report['status'] != 'candidate':
            raise SystemExit(1)
    elif args.operation in ('verify', 'verify-report'):
        if args.operation == 'verify':
            require(args.thresholds is not None, 'Explicit candidate thresholds are required')
            values = json.loads(args.thresholds.read_text())
        else:
            values = artifact['candidate_evaluation']['thresholds']
        report = verify(plan, artifact, values)
        artifact['candidate_evaluation'] = report
        evaluation.legacy.save(args.artifact, artifact)
        print(evaluation.encoded(report))
        if not report['feasible']:
            raise SystemExit(1)
    else:
        measured, pairs = audit(plan, artifact)
        print(evaluation.encoded(dict(status='complete', source_trials=len(measured), pair_trials=len(pairs),
                                      quality_accepted=False, production_registered=False)))
