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
    'judgment-calibration-01': ('stiffness', 'adjustable-bookshelf', ('棚板', '高さ', '大きさの異なる本'), 'product'),
    'judgment-calibration-02': ('abstraction', 'desk-edge-tray', ('机の端', '小さなトレー', 'ペンや眼鏡'), 'product'),
    'judgment-calibration-03': ('formulaic', 'cleaning-tool-fit', ('道具', '掃除'), 'product'),
    'judgment-calibration-04': ('roundabout', 'rain-gauge-battery', ('雨量計', '電池', '測定を始める前'), 'procedure'),
    'judgment-calibration-05': ('natural', 'wort-cooling', ('酵母', '麦汁', '20'), 'procedure'),
    'judgment-calibration-06': ('stiffness', 'gym-lost-property', ('体育館', '30'), 'notice'),
    'judgment-calibration-07': ('abstraction', 'felt-lined-accessory-box', ('厚手のフェルト', '腕時計や鍵', '音'), 'product'),
    'judgment-calibration-08': ('formulaic', 'lacquer-bowl-repair', ('割れた茶わん', '漆'), 'product'),
    'judgment-calibration-09': ('repetition', 'stackable-table-mug', ('重ねてしまえる形', '落ち着いた色', '食洗機'), 'product'),
    'judgment-calibration-10': ('natural', 'hot-plate-warning', ('鉄板', '乾いた鍋つかみ'), 'notice'),
    'judgment-calibration-11': ('roundabout', 'detachable-strap-bag', ('荷物の少ない日', '小ぶりのバッグ', '肩ひもを外', '手提げ'), 'product'),
    'judgment-calibration-12': ('repetition', 'bean-soaking-sequence', ('一晩', '水を捨て', '新しい水'), 'procedure'),
    'judgment-calibration-13': ('natural', 'lightweight-walk-lens', ('単焦点', '180', 'ズーム機能はありません'), 'product'),
    'judgment-calibration-14': ('natural', 'unfinished-thoughts-notebook', ('書きかけ', '同じページ'), 'product'),
    'judgment-calibration-15': ('natural', 'soy-milk-cake-disclosure', ('卵', '豆乳', '保証できません'), 'product'),
}

# Keep calibration out of the legacy rewrite population; both degrees are measured later.
CALIBRATION_INVARIANTS = {
    'judgment-calibration-01': "Keep adjustable shelf height and storage for books of different sizes; do not add capacity or dimensions.",
    'judgment-calibration-02': "Keep the small tray attached to the desk edge, storage for pens and glasses, freed desk space, and easier organization; add no productivity promise.",
    'judgment-calibration-03': "Choose cleaning tools suited to their users with the aim of more pleasant cleaning; add no speed or cost claim.",
    'judgment-calibration-04': "Replace the rain gauge battery before measurement only if it is dead; keep the requirement and polite register.",
    'judgment-calibration-05': "Keep the cooling target of 20 degrees, the order before adding yeast, the heat risk, and every decoded character unchanged.",
    'judgment-calibration-06': "The gym keeps lost property for 30 days and cannot honor return requests afterward; retain polite register.",
    'judgment-calibration-07': "Keep the thick felt lining and reduced noise when setting down a watch or keys; do not claim complete silence or soundproofing.",
    'judgment-calibration-08': "The workshop joins broken tea bowls with lacquer so they can continue to be used; add no claim about strength or safety.",
    'judgment-calibration-09': "Keep stackability, subdued colors suitable for the table, and dishwasher compatibility; do not add heat resistance.",
    'judgment-calibration-10': "Keep the hot surface warning, the prohibition on touching it, dry potholders for the handle, and every decoded character unchanged.",
    'judgment-calibration-11': "Keep suitability for light loads, the small bag, and handheld use conditional on removing the shoulder strap; add no carrying capacity.",
    'judgment-calibration-12': "Soak beans overnight, discard that water, then boil the beans in fresh water; retain the order and polite register.",
    'judgment-calibration-13': "Keep the 180g weight, prime lens, intended ease of casual photography, lack of zoom, and every decoded character unchanged.",
    'judgment-calibration-14': "Keep the notebook for unfinished thoughts, no pressure to reach a conclusion, adding to the same page, and every decoded character unchanged.",
    'judgment-calibration-15': "Keep the egg-free recipe, soy milk, moist texture, shared egg-handling worktop, lack of a contamination guarantee, and every decoded character unchanged.",
}
CALIBRATION = [c for c in CASES if c['language'] == 'ja' and c['id'] in CALIBRATION_MANIFEST]

# Intended hard cases, not measured extrema; do not infer a transferable threshold from fixture success.
BOUNDARY_CANDIDATES = {
    'natural': ('judgment-calibration-13', 'judgment-calibration-14'),
    'unnatural': ('judgment-calibration-01', 'judgment-calibration-02', 'judgment-calibration-11'),
}


def test_ac_08_13_14_calibration_includes_product_register_at_both_boundaries():
    assert Counter((v[0] == 'natural', v[3]) for v in CALIBRATION_MANIFEST.values()) == {
        (True, 'product'): 3, (False, 'product'): 7,
        (True, 'procedure'): 1, (False, 'procedure'): 2,
        (True, 'notice'): 1, (False, 'notice'): 1,
    }
    for label, ids in BOUNDARY_CANDIDATES.items():
        for identity in ids:
            axis, _, _, register = CALIBRATION_MANIFEST[identity]
            assert register == 'product' and (axis == 'natural') == (label == 'natural')


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
    axis, _, anchors, _ = CALIBRATION_MANIFEST[case['id']]
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
