"""New synthetic calibration drafts; owner/native review and the population are incomplete."""
from collections import Counter

import pytest

from copyeditor.judgment_v2 import REFERENCES
from copyeditor.preservation import check
from tests.contracts.test_ctr05_examples import CASES, adapter
from tests.unit.test_judgment_examples import CALIBRATION_MANIFEST, ACCEPTANCE_MANIFEST

# Independent briefs and draft labels stay outside CTR-05 assets and provider inputs.
MANIFEST = {
    'judgment-v2-calibration-01': {'axis': 'stiffness', 'topic': 'observatory-cloud-cancellation', 'origin': 'synthetic-v2/observatory-cloud-cancellation', 'anchors': ['曇りの場合は中止', '当日の17時'], 'witness': 'しっかりと確認することが大切です', 'invariant': 'Keep the cancellation condition, announcement time and polite request; do not promise visibility.'},
    'judgment-v2-calibration-02': {'axis': 'stiffness', 'topic': 'fossil-drawer-catalogue', 'origin': 'synthetic-v2/fossil-drawer-catalogue', 'anchors': ['採集地', '地層の名前'], 'witness': 'と言っても過言ではありません', 'invariant': 'Keep both recorded fields and the possibility of recording them; add no archival guarantee.'},
    'judgment-v2-calibration-03': {'axis': 'abstraction', 'topic': 'stage-surtitles-seat', 'origin': 'synthetic-v2/stage-surtitles-seat', 'anchors': ['客席の左右', '聞こえにくい方'], 'witness': '観劇体験の解像度を上げる', 'invariant': 'Keep the two screen locations and reading support; do not claim visibility from every seat.'},
    'judgment-v2-calibration-04': {'axis': 'abstraction', 'topic': 'seed-loan-ledger', 'origin': 'synthetic-v2/seed-loan-ledger', 'anchors': ['品種と借りた日', '種'], 'witness': 'シームレスに最適化', 'invariant': 'Keep the ledger fields and seed-lending purpose; do not promise a harvest or automated tracking.'},
    'judgment-v2-calibration-05': {'axis': 'formulaic', 'topic': 'tide-board-reading', 'origin': 'synthetic-v2/tide-board-reading', 'anchors': ['満潮の時刻', '日によって変わ'], 'witness': 'いかがでしたか', 'invariant': 'Keep daily variation and checking before a walk; add no statement that walking is safe.'},
    'judgment-v2-calibration-06': {'axis': 'formulaic', 'topic': 'violin-tuning-session', 'origin': 'synthetic-v2/violin-tuning-session', 'anchors': ['オーボエ', '442Hz', 'チューナーを使う場合も'], 'witness': 'ぜひ参考にしてみてください', 'invariant': 'Keep the tuning order, reference instrument and conditional frequency; preserve the technical terms.'},
    'judgment-v2-calibration-07': {'axis': 'roundabout', 'topic': 'kiln-opening-temperature', 'origin': 'synthetic-v2/kiln-opening-temperature', 'anchors': ['80度以下', '扉を開けない', '温度計'], 'witness': 'と言えるでしょう', 'invariant': 'Keep the prohibition and temperature condition; do not introduce a cooling duration.'},
    'judgment-v2-calibration-08': {'axis': 'roundabout', 'topic': 'anonymous-census-table', 'origin': 'synthetic-v2/anonymous-census-table', 'anchors': ['個人の氏名は載せていません', '地区別の人数'], 'witness': '重要なポイントなのではないでしょうか', 'invariant': 'Keep the absence of names and the limit on identifying individuals; add no privacy certification.'},
    'judgment-v2-calibration-09': {'axis': 'repetition', 'topic': 'bicycle-parking-tag', 'origin': 'synthetic-v2/bicycle-parking-tag', 'anchors': ['受付の控え', '紛失した場合', '係員'], 'witness': 'さらに、', 'invariant': 'Keep the shared number, return instruction and lost-tag condition; keep necessary repeated references to the tag.'},
    'judgment-v2-calibration-10': {'axis': 'repetition', 'topic': 'beehive-winter-log', 'origin': 'synthetic-v2/beehive-winter-log', 'anchors': ['巣箱', '測った日', '推測した重さは書きません'], 'witness': 'そして、', 'invariant': 'Keep measured weight and date, the blank-entry condition and the ban on estimated values.'},
}

CASES_V2 = [c for c in CASES if c['id'].startswith('judgment-v2-calibration-')]


def test_new_calibration_subset_has_independent_topics_and_bodies():
    expected = [f'judgment-v2-calibration-{i:02}' for i in range(1, 11)]
    assert list(MANIFEST) == [c['id'] for c in CASES_V2] == expected
    assert Counter(m['axis'] for m in MANIFEST.values()) == dict(
        stiffness=2, abstraction=2, formulaic=2, roundabout=2, repetition=2)
    old_topics = {m[1] for m in (CALIBRATION_MANIFEST | ACCEPTANCE_MANIFEST).values()}
    assert len({m['topic'] for m in MANIFEST.values()}) == len(expected)
    assert not {m['topic'] for m in MANIFEST.values()} & old_topics
    assert len({m['origin'] for m in MANIFEST.values()}) == len(expected)
    other = {c[k] for c in CASES if c not in CASES_V2 for k in ('bad', 'good')} | {r['text'] for r in REFERENCES}
    texts = [c[k] for c in CASES_V2 for k in ('bad', 'good')]
    assert len(set(texts)) == len(texts)
    assert not set(texts) & other
    assert all(a not in b and b not in a for a in texts for b in other)


@pytest.mark.parametrize('case', CASES_V2, ids=lambda c: c['id'])
def test_new_calibration_drafts_remove_worst_expression_and_keep_constraints(case):
    draft = MANIFEST[case['id']]
    assert case['must_change'] and case['bad'] != case['good']
    assert draft['witness'] in case['bad'] and draft['witness'] not in case['good']
    assert all(a in case['bad'] and a in case['good'] for a in draft['anchors'])
    assert draft['invariant'] and draft['origin'] == 'synthetic-v2/' + draft['topic']
    assert case['background'] == {} and case['degree'] == 'polish' and case['format'] == 'text'
    config, snapshot = adapter.environment(case, 'fixture')
    ratio = {k: config['length_ratio.' + k] for k in ('min', 'max')}
    assert not check(case['bad'], case['good'], snapshot.languages['ja'].protected_terms, ratio).failed


def test_incomplete_calibration_cannot_be_selected_as_production_population():
    import judgment_evaluation as evaluation
    with pytest.raises(ValueError, match='not complete'):
        evaluation.freeze(1, name='calibration')
