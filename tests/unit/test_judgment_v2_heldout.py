"""Independent held-out drafts, unmeasured and awaiting owner/native review."""
from collections import Counter

import pytest

from copyeditor.judgment_v2 import REFERENCES
from copyeditor.preservation import check
from tests.contracts.test_ctr05_examples import CASES, adapter
from tests.unit.test_judgment_examples import CALIBRATION_MANIFEST, ACCEPTANCE_MANIFEST
from tests.unit.test_judgment_v2_examples import MANIFEST as CALIBRATION_V2

# Keep draft labels and meaning-breaking contrasts outside provider input assets.
MANIFEST = {
    'judgment-v2-heldout-01': {'axis': 'stiffness', 'topic': 'outdoor-water-meter-inspection', 'origin': 'synthetic-v2-heldout/outdoor-water-meter-inspection', 'anchors': ['建物の外から', '在宅していただく必要はありません'], 'witness': 'しっかりと把握', 'trap': ['在宅していただく必要はありません', '在宅していただく必要があります'], 'invariant': 'Keep exterior inspection and no attendance requirement; do not add an entry or shutoff requirement.'},
    'judgment-v2-heldout-02': {'axis': 'stiffness', 'topic': 'chess-clock-training', 'origin': 'synthetic-v2-heldout/chess-clock-training', 'anchors': ['各15分', '対局を中断して講師を'], 'witness': '非常に重要です', 'trap': ['対局を中断して', '対局を続けて'], 'invariant': 'Keep time per player and the clock-failure stop condition; add no competition rule.'},
    'judgment-v2-heldout-03': {'axis': 'abstraction', 'topic': 'lantern-festival-admission', 'origin': 'synthetic-v2-heldout/lantern-festival-admission', 'anchors': ['大人500円', '小学生以下は無料'], 'witness': '体験価値を最大化', 'trap': ['小学生以下は無料', '小学生以下も有料'], 'invariant': 'Keep the fee and exact free-admission age boundary; add no discount or access promise.'},
    'judgment-v2-heldout-04': {'axis': 'abstraction', 'topic': 'downloadable-pronunciation-lessons', 'origin': 'synthetic-v2-heldout/downloadable-pronunciation-lessons', 'anchors': ['ダウンロードした後', '通信なし', '音声の共有はできません'], 'witness': '理解の解像度を上げます', 'trap': ['音声の共有はできません', '音声の共有ができます'], 'invariant': 'Keep download-before-offline use and the sharing prohibition; add no learning outcome.'},
    'judgment-v2-heldout-05': {'axis': 'formulaic', 'topic': 'freight-lot-label', 'origin': 'synthetic-v2-heldout/freight-lot-label', 'anchors': ['品番とロット番号', '出荷せずに担当者へ'], 'witness': 'いかがでしたか', 'trap': ['出荷せずに', '出荷してから'], 'invariant': 'Keep both identifiers and the unreadable-label exception; do not replace lot number with product number.'},
    'judgment-v2-heldout-06': {'axis': 'formulaic', 'topic': 'translation-volunteer-workshop', 'origin': 'synthetic-v2-heldout/translation-volunteer-workshop', 'anchors': ['経験のない方も参加', '開催日の前日まで'], 'witness': 'ぜひ参考にしてみてください', 'trap': ['経験のない方も参加できます', '経験のない方は参加できません'], 'invariant': 'Keep beginner eligibility and the application deadline; add no selection guarantee.'},
    'judgment-v2-heldout-07': {'axis': 'roundabout', 'topic': 'confidential-paper-collection', 'origin': 'synthetic-v2-heldout/confidential-paper-collection', 'anchors': ['紙だけ', '毎週水曜日'], 'witness': '必要になるのではないでしょうか', 'trap': ['紙だけを入れてください', '紙以外も入れてください'], 'invariant': 'Keep paper-only collection, clip removal and weekday; add no destruction certification.'},
    'judgment-v2-heldout-08': {'axis': 'roundabout', 'topic': 'swimming-lane-reservation', 'origin': 'synthetic-v2-heldout/swimming-lane-reservation', 'anchors': ['練習会の時間中', '2コース', '残りのコース'], 'witness': 'お伝えしたいと思います', 'trap': ['残りのコース', '貸切のコース'], 'invariant': 'Keep the time-limited two-lane reservation and public access to remaining lanes; do not close the entire pool.'},
    'judgment-v2-heldout-09': {'axis': 'repetition', 'topic': 'puppet-making-child-session', 'origin': 'synthetic-v2-heldout/puppet-making-child-session', 'anchors': ['小学生', '材料は会場で渡します', '完成した人形は持ち帰れます'], 'witness': 'さらに、', 'trap': ['完成した人形は持ち帰れます', '完成した人形は持ち帰れません'], 'invariant': 'Keep the age group, required guardian, supplied materials and take-home permission.'},
    'judgment-v2-heldout-10': {'axis': 'repetition', 'topic': 'coffee-roast-order', 'origin': 'synthetic-v2-heldout/coffee-roast-order', 'anchors': ['注文を受けてから焙煎', '焙煎の翌日', '挽き目は注文時に'], 'witness': 'そして、', 'trap': ['挽き目は注文時に指定してください', '挽き目は発送後に指定してください'], 'invariant': 'Keep roast-after-order, dispatch timing and optional grinding with advance specification; add no arrival guarantee.'},
    'judgment-v2-heldout-11': {'axis': 'stiffness', 'topic': 'coin-laundry-finish-alert', 'origin': 'synthetic-v2-heldout/coin-laundry-finish-alert', 'anchors': ['登録した携帯電話', '洗濯を始める前'], 'witness': 'しっかり行う', 'trap': ['洗濯を始める前', '洗濯を始めた後'], 'invariant': 'Keep the registered phone and registration timing; do not promise alerts without registration.'},
    'judgment-v2-heldout-12': {'axis': 'stiffness', 'topic': 'rowing-boat-return-pier', 'origin': 'synthetic-v2-heldout/rowing-boat-return-pier', 'anchors': ['借りた桟橋', '別の桟橋では返却を受け付けません'], 'witness': '返却整合', 'trap': ['別の桟橋では返却を受け付けません', '別の桟橋でも返却を受け付けます'], 'invariant': 'Keep the same-pier return requirement; add no rental period or navigation advice.'},
    'judgment-v2-heldout-13': {'axis': 'abstraction', 'topic': 'carbonless-delivery-form', 'origin': 'synthetic-v2-heldout/carbonless-delivery-form', 'anchors': ['上の紙は納品先へ', '下の紙は控え'], 'witness': '書類体験をシームレスに最適化', 'trap': ['上の紙は納品先へ渡し', '下の紙は納品先へ渡し'], 'invariant': 'Keep transfer behavior and which sheet goes to each recipient; do not reverse the copies.'},
    'judgment-v2-heldout-14': {'axis': 'abstraction', 'topic': 'repair-cafe-number-ticket', 'origin': 'synthetic-v2-heldout/repair-cafe-number-ticket', 'anchors': ['受付順', '修理の完了を約束するものではありません'], 'witness': '解決への架け橋', 'trap': ['修理の完了を約束するものではありません', '修理の完了を約束するものです'], 'invariant': 'Keep queue order and the explicit lack of a repair-completion promise.'},
    'judgment-v2-heldout-15': {'axis': 'formulaic', 'topic': 'shared-bread-oven-tray', 'origin': 'synthetic-v2-heldout/shared-bread-oven-tray', 'anchors': ['自分の天板を持参', '天板を貸し出していません'], 'witness': 'ぜひ参考にして', 'trap': ['天板を貸し出していません', '天板を貸し出しています'], 'invariant': 'Keep the bring-your-own tray requirement and absence of rentals; add no equipment compatibility advice.'},
    'judgment-v2-heldout-16': {'axis': 'formulaic', 'topic': 'guest-wifi-expiration', 'origin': 'synthetic-v2-heldout/guest-wifi-expiration', 'anchors': ['当日のみ', '再発行を依頼'], 'witness': 'いかがでしたか', 'trap': ['当日のみ使えます', '翌日も使えます'], 'invariant': 'Keep the same-day validity and next-day reissue condition; do not include credentials.'},
    'judgment-v2-heldout-17': {'axis': 'roundabout', 'topic': 'parcel-forwarding-start-date', 'origin': 'synthetic-v2-heldout/parcel-forwarding-start-date', 'anchors': ['申込みの3日後', 'すでに発送した荷物には反映され'], 'witness': 'ご理解いただく形', 'trap': ['申込みの3日後', '申込みの当日'], 'invariant': 'Keep the delay and exclusion of already dispatched parcels; add no delivery guarantee.'},
    'judgment-v2-heldout-18': {'axis': 'roundabout', 'topic': 'weekend-building-entry', 'origin': 'synthetic-v2-heldout/weekend-building-entry', 'anchors': ['登録済みの入館カード', 'カードの登録は平日に'], 'witness': '重要なポイントと言えるでしょう', 'trap': ['土曜日は、登録済みの入館カードで入館できます', '日曜日は、登録済みの入館カードで入館できます'], 'invariant': 'Keep Saturday access, Sunday prohibition and weekday registration; add no entry procedure.'},
    'judgment-v2-heldout-19': {'axis': 'repetition', 'topic': 'campground-numbered-pitch', 'origin': 'synthetic-v2-heldout/campground-numbered-pitch', 'anchors': ['受付で指定した区画', '空いていても無断で移らない'], 'witness': 'そして、', 'trap': ['空いていても無断で移らないでください', '空いていれば無断で移ってください'], 'invariant': 'Keep assigned placement, tag return and permission before moving; preserve repeated plot references.'},
    'judgment-v2-heldout-20': {'axis': 'repetition', 'topic': 'charity-book-sale-payments', 'origin': 'synthetic-v2-heldout/charity-book-sale-payments', 'anchors': ['会場費を差し引いて', '現金のみ', '袋を持参'], 'witness': 'さらに、', 'trap': ['会場費を差し引いて寄付します', '全額を寄付します'], 'invariant': 'Keep net proceeds rather than gross donation, cash-only payment and bring-your-own bags.'},
}

HELDOUT = [c for c in CASES if c['id'].startswith('judgment-v2-heldout-')]


def test_heldout_subset_has_independent_topics_origins_and_bodies():
    expected = [f'judgment-v2-heldout-{i:02}' for i in range(1, 21)]
    assert list(MANIFEST) == [c['id'] for c in HELDOUT] == expected
    assert Counter(m['axis'] for m in MANIFEST.values()) == dict(
        stiffness=4, abstraction=4, formulaic=4, roundabout=4, repetition=4)
    old_topics = {m[1] for m in (CALIBRATION_MANIFEST | ACCEPTANCE_MANIFEST).values()}
    old_topics |= {m['topic'] for m in CALIBRATION_V2.values()}
    assert len({m['topic'] for m in MANIFEST.values()}) == len(expected)
    assert not {m['topic'] for m in MANIFEST.values()} & old_topics
    origins = {m['origin'] for m in MANIFEST.values()}
    assert len(origins) == len(expected)
    assert not origins & {m['origin'] for m in CALIBRATION_V2.values()}
    other = {c[k] for c in CASES if c not in HELDOUT for k in ('bad', 'good')} | {r['text'] for r in REFERENCES}
    texts = [c[k] for c in HELDOUT for k in ('bad', 'good')]
    assert len(set(texts)) == len(texts)
    assert not set(texts) & other
    assert all(a not in b and b not in a for a in texts for b in other)


@pytest.mark.parametrize('case', HELDOUT, ids=lambda c: c['id'])
def test_heldout_improvement_preserves_conditions_and_exposes_meaning_traps(case):
    draft = MANIFEST[case['id']]
    assert case['must_change'] and case['bad'] != case['good']
    assert draft['witness'] in case['bad'] and draft['witness'] not in case['good']
    assert all(a in case['bad'] and a in case['good'] for a in draft['anchors'])
    source, broken = draft['trap']
    assert source in case['bad'] and source in case['good'] and broken not in case['good']
    assert case['good'].replace(source, broken) != case['good']
    assert draft['invariant'] and draft['origin'] == 'synthetic-v2-heldout/' + draft['topic']
    assert case['background'] == {} and case['degree'] == 'polish' and case['format'] == 'text'
    config, snapshot = adapter.environment(case, 'fixture')
    ratio = {k: config['length_ratio.' + k] for k in ('min', 'max')}
    assert not check(case['bad'], case['good'], snapshot.languages['ja'].protected_terms, ratio).failed


def test_incomplete_heldout_cannot_be_selected_for_acceptance():
    import judgment_evaluation as evaluation
    with pytest.raises(ValueError, match='not complete'):
        evaluation.freeze(1, name='acceptance')
