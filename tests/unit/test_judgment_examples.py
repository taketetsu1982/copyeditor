"""Calibration drafts only; native review and owner labels have not been obtained."""
from collections import Counter
from hashlib import sha256

import pytest

from copyeditor.judgment import REFERENCES
from copyeditor.preservation import check
from tests.contracts.test_ctr05_examples import CASES, ROOT, adapter

# These independent synthetic briefs are not derived from policy references or legacy examples.
# Keep evaluation labels outside CTR-05 assets so they cannot become tool input.
CALIBRATION_MANIFEST = {
    'judgment-calibration-01': ('stiffness', 'display-case-cleaning', ('内側',)),
    'judgment-calibration-02': ('abstraction', 'bakery-timing', ('パン', '店頭')),
    'judgment-calibration-03': ('formulaic', 'cleaning-tool-fit', ('道具', '掃除')),
    'judgment-calibration-04': ('roundabout', 'rain-gauge-battery', ('雨量計', '電池', '測定を始める前')),
    'judgment-calibration-05': ('natural', 'wort-cooling', ('酵母', '麦汁', '20')),
    'judgment-calibration-06': ('stiffness', 'gym-lost-property', ('体育館', '30')),
    'judgment-calibration-07': ('abstraction', 'library-aisle-width', ('図書館', '車いす')),
    'judgment-calibration-08': ('formulaic', 'lacquer-bowl-repair', ('割れた茶わん', '漆')),
    'judgment-calibration-09': ('repetition', 'trail-map-distribution', ('登山道', '売店', '無料', '持ち帰れ')),
    'judgment-calibration-10': ('natural', 'hot-plate-warning', ('鉄板', '乾いた鍋つかみ')),
    'judgment-calibration-11': ('roundabout', 'unreserved-family-bath', ('家族風呂', '空きがある場合に限って')),
    'judgment-calibration-12': ('repetition', 'bean-soaking-sequence', ('一晩', '水を捨て', '新しい水')),
    'judgment-calibration-13': ('natural', 'prime-lens-framing', ('単焦点', 'ズームはできない')),
    'judgment-calibration-14': ('natural', 'wet-dry-cloth-separation', ('乾いた布', 'ぬれた布')),
    'judgment-calibration-15': ('natural', 'shared-egg-worktop', ('卵', '保証できません')),
}

# Keep calibration out of the legacy rewrite population; both degrees are measured later.
CALIBRATION_INVARIANTS = {
    'judgment-calibration-01': "The borrower must clean the inside of the loaned display case when returning it; retain polite register.",
    'judgment-calibration-02': "Post the bread baking times at the shop to help customers decide when to buy; do not promise availability.",
    'judgment-calibration-03': "Choose cleaning tools suited to their users with the aim of more pleasant cleaning; add no speed or cost claim.",
    'judgment-calibration-04': "Replace the rain gauge battery before measurement only if it is dead; keep the requirement and polite register.",
    'judgment-calibration-05': "Keep the cooling target of 20 degrees, the order before adding yeast, the heat risk, and every decoded character unchanged.",
    'judgment-calibration-06': "The gym keeps lost property for 30 days and cannot honor return requests afterward; retain polite register.",
    'judgment-calibration-07': "Secure aisle width to make wheelchair movement easier in the library; this is an aim, not a measured result.",
    'judgment-calibration-08': "The workshop joins broken tea bowls with lacquer so they can continue to be used; add no claim about strength or safety.",
    'judgment-calibration-09': "Trail maps are distributed at the shop, are free, and may be taken home; keep all three facts.",
    'judgment-calibration-10': "Keep the hot surface warning, the prohibition on touching it, dry potholders for the handle, and every decoded character unchanged.",
    'judgment-calibration-11': "People without a family-bath reservation may use it only when there is a vacancy; do not promise a vacancy.",
    'judgment-calibration-12': "Soak beans overnight, discard that water, then boil the beans in fresh water; retain the order and polite register.",
    'judgment-calibration-13': "Keep the prime-lens property, the inability to zoom, the position adjustment, and every decoded character unchanged.",
    'judgment-calibration-14': "Keep the shelf for dry cloths, the bucket for wet cloths, their separation, and every decoded character unchanged.",
    'judgment-calibration-15': "Keep the shared egg-handling worktop and the lack of a guarantee against egg contamination; preserve every decoded character unchanged.",
}
CALIBRATION = [c for c in CASES if c['language'] == 'ja' and c['id'] in CALIBRATION_MANIFEST]


def test_ac_08_13_14_ctr05_calibration_composition_and_independent_sources():
    ids = [f'judgment-calibration-{i:02}' for i in range(1, 16)]
    assert list(CALIBRATION_MANIFEST) == [c['id'] for c in CALIBRATION] == ids
    assert set(CALIBRATION_INVARIANTS) == set(ids)
    assert [c['id'] for c in CASES if c['language'] == 'ja' and c['id'].startswith('judgment-calibration-')] == ids
    assert Counter(v[0] for v in CALIBRATION_MANIFEST.values()) == dict(
        stiffness=2, abstraction=2, formulaic=2, roundabout=2, repetition=2, natural=5)
    assert CALIBRATION[2]['reason'].startswith('Generic wording:')
    assert CALIBRATION[7]['reason'].startswith('Formulaic ending:')
    assert len({v[1] for v in CALIBRATION_MANIFEST.values()}) == 15
    assert len({c['bad'] for c in CALIBRATION}) == len({c['good'] for c in CALIBRATION}) == 15
    others = {r['text'] for r in REFERENCES} | {c[k] for c in CASES if c not in CALIBRATION for k in ('bad', 'good')}
    assert not {c[k] for c in CALIBRATION for k in ('bad', 'good')} & others
    assert all(r['text'] not in c[k] and c[k] not in r['text']
               for c in CALIBRATION for k in ('bad', 'good') for r in REFERENCES)
    for start in (0, 5, 10):
        group = CALIBRATION[start:start + 5]
        assert any(not c['must_change'] for c in group)
        assert all(c['format'] == 'text' and c['background'] == {} and 'context' not in c for c in group)
    legacy = b''.join((ROOT / f'examples/ja/rewrite-{i:02}.yaml').read_bytes() for i in range(1, 25))
    assert sha256(legacy).hexdigest() == 'a1272facdb93e431ba60424d2c2265dec35444c2f2d84fdd4508dd5060c5c2a5'


@pytest.mark.parametrize('case', CALIBRATION, ids=lambda c: c['id'])
def test_ac_08_13_14_ctr05_calibration_preserves_facts_terms_and_natural_text(case):
    axis, _, anchors = CALIBRATION_MANIFEST[case['id']]
    assert case['must_change'] == (axis != 'natural')
    assert (case['bad'] == case['good']) == (axis == 'natural')
    assert case['degree'] == 'polish' and 'rewrite_expectations' not in case
    assert CALIBRATION_INVARIANTS[case['id']] and case['reason']
    config, snapshot = adapter.environment(case, 'fixture')
    ratio = {key: config['length_ratio.' + key] for key in ('min', 'max')}
    assert not check(case['bad'], case['good'], snapshot.languages['ja'].protected_terms, ratio).failed
    # Anchors supplement preservation; they do not certify semantic equivalence or native quality.
    assert all(token in case['bad'] and token in case['good'] for token in anchors)
    if case['id'] in ('judgment-calibration-05', 'judgment-calibration-10', 'judgment-calibration-14', 'judgment-calibration-15'):
        assert case['bad'].count(anchors[0]) >= 2
