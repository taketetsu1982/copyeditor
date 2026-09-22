"""AC-08-3/5/8: shared immutable gate, exact boundaries and unregistered production values."""
from dataclasses import FrozenInstanceError
from decimal import localcontext
import math

import pytest

from copyeditor import judgment, judgment_v2 as subject
from copyeditor.requests import ValidationError


@pytest.fixture
def selected():
    return synthetic()


def synthetic(**changes):
    definition = dict(id="synthetic-only", floor=0.53, gap=0.1, meaning_floor=0.7)
    definition.update(changes)
    return subject.snapshot(subject.POLICY_ID, definition['id'],
                            thresholds={definition['id']: definition},
                            pairs={(subject.POLICY_ID, definition['id'])})


def test_production_remains_unregistered_and_old_registry_is_retired():
    assert not subject.THRESHOLDS and not subject.COMPATIBLE_PAIRS
    assert not hasattr(judgment, "POLICIES") and not hasattr(judgment, "THRESHOLDS")
    with pytest.raises(ValidationError):
        subject.snapshot(subject.POLICY_ID, subject.THRESHOLD_ID)
    with pytest.raises(TypeError):
        subject.THRESHOLDS[subject.THRESHOLD_ID] = {}


def test_policy_pins_questions_references_and_serialization():
    policy = subject.POLICY
    assert subject.POLICY_HASH == "2107a389d5ed2faa50461aa50762e24b6a4b65be1bde8a980101c8760d57cbee"
    assert policy['question_order'] == {'detect': ('gate',), 'verify': ('gate', 'meaning')}
    assert policy['question_type'] == 'Noul'
    assert 'worst single spot' in subject.GATE and 'worst removable expression' in subject.PREFIX
    assert all(word in subject.GATE for word in (
        'stiff', 'abstract', 'generic', 'formulaic', 'roundabout', 'mechanically repetitive'))
    assert len(subject.REFERENCES) == 6
    for n in range(3):
        natural, unnatural = subject.REFERENCES[n], subject.REFERENCES[n + 3]
        assert natural['id'] == f'ref-natural-{n + 1}' and natural['label'] == 'natural'
        assert unnatural['id'] == f'ref-unnatural-{n + 1}' and unnatural['label'] == 'unnatural'
        assert unnatural['text'].startswith(natural['text']) and unnatural['text'] != natural['text']
    assert policy['background_fields'] == ('audience', 'purpose', 'message')
    assert not {'action', 'desired_style', 'tone'} & set(policy['state_fields']['detect'])
    assert policy['state_fields']['verify'] == (*policy['state_fields']['detect'], 'originals')
    assert policy['packing_version'] == 'request-pack-v2'


def test_snapshot_is_detached_from_mutable_registry(selected):
    definition = dict(selected.threshold)
    result = subject.snapshot(subject.POLICY_ID, definition['id'],
                              thresholds={definition['id']: definition},
                              pairs={(subject.POLICY_ID, definition['id'])})
    definition['floor'] = 1
    assert result.threshold['floor'] == 0.53
    with pytest.raises(TypeError):
        result.threshold['floor'] = 1
    with pytest.raises(TypeError):
        result.policy['references'][0]['text'] = 'changed'
    with pytest.raises(FrozenInstanceError):
        result.policy_hash = 'changed'
    assert result.policy_hash == synthetic(gap=0.2).policy_hash
    assert result.threshold_hash != synthetic(gap=0.2).threshold_hash


@pytest.mark.parametrize('policy,threshold,pairs', [
    ('unknown', 'synthetic-only', {('unknown', 'synthetic-only')}),
    (subject.POLICY_ID, 'unknown', {(subject.POLICY_ID, 'unknown')}),
    ('unknown', 'unknown', {('unknown', 'unknown')}),
    (subject.POLICY_ID, 'synthetic-only', set()),
])
def test_unknown_or_incompatible_pair_is_rejected(policy, threshold, pairs, selected):
    with pytest.raises(ValidationError):
        subject.snapshot(policy, threshold, thresholds={'synthetic-only': selected.threshold}, pairs=pairs)


@pytest.mark.parametrize('key', ['floor', 'gap', 'meaning_floor'])
@pytest.mark.parametrize('value', [True, None, '0.5', -0.1, 1.01, math.nan, math.inf])
def test_invalid_threshold_values_are_rejected(key, value):
    with pytest.raises(ValidationError):
        synthetic(**{key: value})


def test_zero_gap_is_rejected_but_domain_endpoints_are_valid():
    with pytest.raises(ValidationError):
        synthetic(gap=0)
    synthetic(floor=0, gap=1, meaning_floor=1)
    synthetic(floor=1, gap=1, meaning_floor=0)


@pytest.mark.parametrize('value,expected', [
    (math.nextafter(0.53, 0), 'insufficient'), (0.53, 'eligible'),
    (math.nextafter(0.53, 1), 'eligible'),
])
def test_detection_has_only_floor_classification(selected, value, expected):
    assert subject.classify_detection(value, selected) == dict(
        status=expected, reason='evaluated', probability=value)


@pytest.mark.parametrize('candidate,meaning,expected', [
    (0.7, 0.7, True), (math.nextafter(0.7, 1), 0.7, False),
    (math.nextafter(0.7, 0), 0.7, True), (0.7, math.nextafter(0.7, 0), False),
    (0.7, math.nextafter(0.7, 1), True), (0.8, 1, False), (0.9, 1, False),
])
def test_verification_uses_delta_and_meaning_without_rounding(selected, candidate, meaning, expected):
    with localcontext() as context:
        context.prec = 1
        assert subject.classify_verification(0.8, candidate, meaning, selected) is expected
    assert subject.classify_verification(0.9, 0.8, 1, selected)
    assert not subject.classify_verification(0.1, 0.05, 1, selected)


@pytest.mark.parametrize('invalid', [True, None, '0.5', -1, 2, math.nan, math.inf])
def test_invalid_scores_fail_even_if_another_condition_already_fails(selected, invalid):
    with pytest.raises(ValidationError):
        subject.classify_detection(invalid, selected)
    for values in ((invalid, 1, 0), (0, invalid, 0), (0, 1, invalid)):
        with pytest.raises(ValidationError):
            subject.classify_verification(*values, selected)
