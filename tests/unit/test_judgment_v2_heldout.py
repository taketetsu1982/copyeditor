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
    'judgment-v2-heldout-21': {'axis': 'stiffness', 'topic': 'street-leaf-collection-bags', 'origin': 'synthetic-v2-heldout/street-leaf-collection-bags', 'anchors': ['市の回収袋', '枝や石は入れない'], 'witness': '非常に大切です', 'trap': ['枝や石は入れないでください', '枝や石も入れてください'], 'invariant': 'Keep the designated bag and excluded materials; add no collection schedule.'},
    'judgment-v2-heldout-22': {'axis': 'stiffness', 'topic': 'remote-meeting-speaking-card', 'origin': 'synthetic-v2-heldout/remote-meeting-speaking-card', 'anchors': ['名前の横の札', '司会者が順番に指名'], 'witness': '発言可視', 'trap': ['司会者が順番に指名します', '各自が自由に発言します'], 'invariant': 'Keep the signaling action and moderator-controlled order; do not add automatic microphone behavior.'},
    'judgment-v2-heldout-23': {'axis': 'abstraction', 'topic': 'shoe-width-measurement-sheet', 'origin': 'synthetic-v2-heldout/shoe-width-measurement-sheet', 'anchors': ['足長と足囲', '左右別々'], 'witness': '歩行体験を最適化', 'trap': ['左右別々に記録します', '左右の平均だけを記録します'], 'invariant': 'Keep both measurements and separate sides; add no fit or health guarantee.'},
    'judgment-v2-heldout-24': {'axis': 'abstraction', 'topic': 'photo-exhibit-voting-stickers', 'origin': 'synthetic-v2-heldout/photo-exhibit-voting-stickers', 'anchors': ['一人につき', '1枚', '作品の番号の下'], 'witness': '感性の共鳴を生み出す', 'trap': ['投票シールを1枚渡します', '投票シールを何枚でも渡します'], 'invariant': 'Keep one vote per person and sticker location; add no selection or prize rule.'},
    'judgment-v2-heldout-25': {'axis': 'formulaic', 'topic': 'mountain-hut-bedding-booking', 'origin': 'synthetic-v2-heldout/mountain-hut-bedding-booking', 'anchors': ['宿泊予約と一緒', '当日の追加申込みは受け付けません'], 'witness': '意識しましょう', 'trap': ['当日の追加申込みは受け付けません', '当日の追加申込みも受け付けます'], 'invariant': 'Keep joint booking and no same-day additions; add no equipment or route advice.'},
    'judgment-v2-heldout-26': {'axis': 'formulaic', 'topic': 'parking-pass-holiday-exclusion', 'origin': 'synthetic-v2-heldout/parking-pass-holiday-exclusion', 'anchors': ['平日の入庫', '土日と祝日は使えません', '券の裏面'], 'witness': 'ぜひ参考にしてみてください', 'trap': ['土日と祝日は使えません', '土日と祝日も使えます'], 'invariant': 'Keep weekday entry eligibility, excluded days and printed expiry location.'},
    'judgment-v2-heldout-27': {'axis': 'roundabout', 'topic': 'public-address-test-schedule', 'origin': 'synthetic-v2-heldout/public-address-test-schedule', 'anchors': ['毎月15日の正午', '冒頭でも試験と案内'], 'witness': 'ご理解いただく形', 'trap': ['冒頭でも試験と案内します', '冒頭では実際の災害と案内します'], 'invariant': 'Keep the test date/time and explicit test announcement; do not describe a real emergency.'},
    'judgment-v2-heldout-28': {'axis': 'roundabout', 'topic': 'concert-seat-change-request', 'origin': 'synthetic-v2-heldout/concert-seat-change-request', 'anchors': ['空席がある場合のみ', '希望する席を必ず用意できるわけでは'], 'witness': 'お伝えしておきたいと思います', 'trap': ['空席がある場合のみ受け付けます', '空席がない場合も受け付けます'], 'invariant': 'Keep vacancy as a condition and the lack of a preferred-seat guarantee.'},
    'judgment-v2-heldout-29': {'axis': 'repetition', 'topic': 'circuit-board-revision-parts', 'origin': 'synthetic-v2-heldout/circuit-board-revision-parts', 'anchors': ['版番号', '組み立てる前', '版番号が違う部品表は使わない'], 'witness': 'そして、', 'trap': ['版番号が違う部品表は使わないでください', '版番号が違う部品表でも使ってください'], 'invariant': 'Keep matching revisions, the before-assembly check and mismatched-list prohibition; add no electrical instructions.'},
    'judgment-v2-heldout-30': {'axis': 'repetition', 'topic': 'warehouse-key-return-log', 'origin': 'synthetic-v2-heldout/warehouse-key-return-log', 'anchors': ['借りた人が受付へ', '返却時刻を台帳に', '別の人へ直接渡さない'], 'witness': 'さらに、', 'trap': ['別の人へ直接渡さないでください', '別の人へ直接渡してください'], 'invariant': 'Keep borrower responsibility, logging, no direct handoff and the closed-desk exception.'},
    'judgment-v2-heldout-31': {'axis': 'natural', 'control': 'technical', 'topic': 'acoustic-spectrum-report', 'origin': 'synthetic-v2-heldout/acoustic-spectrum-report', 'anchors': ['周波数', '音圧レベル', 'Hz', 'dB'], 'witness': '周波数', 'invariant': 'Keep both axis assignments and units; keep every decoded character unchanged.'},
    'judgment-v2-heldout-32': {'axis': 'natural', 'control': 'technical', 'topic': 'fermentation-density-log', 'origin': 'synthetic-v2-heldout/fermentation-density-log', 'anchors': ['測定日と比重', 'そのまま記録', '別の欄'], 'witness': '比重', 'invariant': 'Keep raw and corrected readings in different fields; keep every decoded character unchanged.'},
    'judgment-v2-heldout-33': {'axis': 'natural', 'control': 'repetition', 'topic': 'costume-rental-cover', 'origin': 'synthetic-v2-heldout/costume-rental-cover', 'anchors': ['カバー', '貸出番号', '受付へ連絡'], 'witness': 'カバー', 'invariant': 'Keep the return container, identifier and missing-cover exception; repeated references distinguish the assigned cover.'},
    'judgment-v2-heldout-34': {'axis': 'natural', 'control': 'technical', 'topic': 'braille-page-index', 'origin': 'synthetic-v2-heldout/braille-page-index', 'anchors': ['点字', '墨字', '右上', '一致しない'], 'witness': '点字', 'invariant': 'Keep the two numbering systems, position and inquiry instruction; keep every decoded character unchanged.'},
    'judgment-v2-heldout-35': {'axis': 'natural', 'control': 'repetition', 'topic': 'classroom-homework-box', 'origin': 'synthetic-v2-heldout/classroom-homework-box', 'anchors': ['提出箱', '朝の会が始まる前', '先生へ渡'], 'witness': '提出箱', 'invariant': 'Keep ordinary and late-submission destinations and the collection time; keep every decoded character unchanged.'},
    'judgment-v2-heldout-36': {'axis': 'natural', 'control': 'condition', 'topic': 'inscription-rubbing-copy', 'origin': 'synthetic-v2-heldout/inscription-rubbing-copy', 'anchors': ['展示用の複製', '実物の石碑は会場にありません', '読めない箇所'], 'witness': '複製', 'invariant': 'Keep the replica status, absent original and unreadable portions; keep every decoded character unchanged.'},
    'judgment-v2-heldout-37': {'axis': 'natural', 'control': 'repetition', 'topic': 'rental-rainwear-drying', 'origin': 'synthetic-v2-heldout/rental-rainwear-drying', 'anchors': ['雨具', '袋に密閉せず', '返却期限は延長されません'], 'witness': '雨具', 'invariant': 'Keep drying, the wet-return exception and unchanged deadline; keep every decoded character unchanged.'},
    'judgment-v2-heldout-38': {'axis': 'natural', 'control': 'technical', 'topic': 'sample-timeout-setting', 'origin': 'synthetic-v2-heldout/sample-timeout-setting', 'anchors': ['timeout_ms', '3000', 'ミリ秒', '0を指定'], 'witness': 'timeout_ms', 'invariant': 'Keep the sample value, unit, zero behavior and separate production choice; keep every decoded character unchanged.'},
    'judgment-v2-heldout-39': {'axis': 'natural', 'control': 'style', 'topic': 'travel-ticket-album', 'origin': 'synthetic-v2-heldout/travel-ticket-album', 'anchors': ['切符', '一枚ずつ', '日付', '寄り道'], 'witness': 'あの日の寄り道', 'invariant': 'Keep the concrete memory invitation and permissive product voice; keep every decoded character unchanged.'},
    'judgment-v2-heldout-40': {'axis': 'natural', 'control': 'style', 'topic': 'movable-type-display', 'origin': 'synthetic-v2-heldout/movable-type-display', 'anchors': ['活字', '側面についた傷', '触れずに'], 'witness': '近くでご覧ください', 'invariant': 'Keep the gentle invitation and no-touch condition; keep every decoded character unchanged.'},
}

HELDOUT = [c for c in CASES if c['id'].startswith('judgment-v2-heldout-')]


def test_heldout_subset_has_independent_topics_origins_and_bodies():
    expected = [f'judgment-v2-heldout-{i:02}' for i in range(1, 41)]
    assert list(MANIFEST) == [c['id'] for c in HELDOUT] == expected
    assert Counter(m['axis'] for m in MANIFEST.values()) == dict(
        stiffness=6, abstraction=6, formulaic=6, roundabout=6, repetition=6, natural=10)
    old_topics = {m[1] for m in (CALIBRATION_MANIFEST | ACCEPTANCE_MANIFEST).values()}
    old_topics |= {m['topic'] for m in CALIBRATION_V2.values()}
    assert len({m['topic'] for m in MANIFEST.values()}) == len(expected)
    assert not {m['topic'] for m in MANIFEST.values()} & old_topics
    origins = {m['origin'] for m in MANIFEST.values()}
    assert len(origins) == len(expected)
    assert not origins & {m['origin'] for m in CALIBRATION_V2.values()}
    other = {c[k] for c in CASES if c not in HELDOUT for k in ('bad', 'good')} | {r['text'] for r in REFERENCES}
    texts = [c[k] for c in HELDOUT for k in ('bad', 'good')]
    assert len({c['bad'] for c in HELDOUT}) == len({c['good'] for c in HELDOUT}) == 40
    assert not set(texts) & other
    assert all(a not in b and b not in a for a in texts for b in other)


@pytest.mark.parametrize('case', HELDOUT, ids=lambda c: c['id'])
def test_heldout_improvement_preserves_conditions_and_exposes_meaning_traps(case):
    draft = MANIFEST[case['id']]
    natural = draft['axis'] == 'natural'
    assert case['must_change'] is not natural
    assert (case['bad'] == case['good']) is natural
    assert draft['witness'] in case['bad']
    assert (draft['witness'] in case['good']) is natural
    assert all(a in case['bad'] and a in case['good'] for a in draft['anchors'])
    if not natural:
        source, broken = draft['trap']
        assert source in case['bad'] and source in case['good'] and broken not in case['good']
        assert case['good'].replace(source, broken) != case['good']
    assert draft['invariant'] and draft['origin'] == 'synthetic-v2-heldout/' + draft['topic']
    assert case['background'] == {} and case['degree'] == 'polish' and case['format'] == 'text'
    config, snapshot = adapter.environment(case, 'fixture')
    ratio = {k: config['length_ratio.' + k] for k in ('min', 'max')}
    assert not check(case['bad'], case['good'], snapshot.languages['ja'].protected_terms, ratio).failed


def test_complete_heldout_freezes_all_trials_separate_from_calibration():
    import judgment_evaluation as evaluation
    plan = evaluation.freeze(1, name='acceptance')
    population = plan['sets'][0]
    assert population['name'] == 'judgment-acceptance-v4'
    assert population['population_hash'] == evaluation.POPULATION_PINS['acceptance'][2]
    assert population['owner_labels'] == population['native_review'] == 'pending'
    assert Counter(c['kind'] for c in population['cases']) == dict(problem=30, natural=10)
    assert all(c['origin'] == 'synthetic-v2:' + c['id'] and c['sha256'] for c in population['cases'])
    assert len(plan['planned_trials']) == len(plan['request_layouts']) == 800
    assert set(t['example_id'] for t in plan['planned_trials']) == set(MANIFEST)
    assert not set(MANIFEST) & set(CALIBRATION_V2)
    assert all(t['layout'] == 'text' for t in plan['planned_trials'])
    assert len(evaluation.population('existing')) == 24
    assert len(evaluation.population('calibration')) == 30
    assert not plan['acceptance_criteria']['quality_accepted']
    assert Counter(m.get('control') for m in MANIFEST.values() if m['axis'] == 'natural') == dict(
        technical=4, repetition=3, condition=1, style=2)


@pytest.mark.parametrize('change', ['empty', 'missing', 'extra', 'changed', 'historical', 'calibration'])
def test_heldout_hash_rejects_missing_or_contaminated_assets(tmp_path, monkeypatch, change):
    import shutil
    import judgment_evaluation as evaluation
    source = evaluation.legacy.ROOT
    target = tmp_path / 'examples/ja'
    target.mkdir(parents=True)
    if change != 'empty':
        for path in (source / 'examples/ja').glob('judgment-v2-heldout-*.yaml'):
            shutil.copyfile(path, target / path.name)
        first = target / 'judgment-v2-heldout-01.yaml'
        if change == 'missing': first.unlink()
        elif change == 'extra': shutil.copyfile(first, target / 'judgment-v2-heldout-99.yaml')
        elif change == 'changed': first.write_text(first.read_text() + '\n# changed\n')
        else:
            prefix = 'judgment-acceptance' if change == 'historical' else 'judgment-v2-calibration'
            shutil.copyfile(source / f'examples/ja/{prefix}-01.yaml', first)
    monkeypatch.setattr(evaluation.legacy, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='Missing or changed evaluation population'):
        evaluation.population('acceptance')


def test_complete_heldout_does_not_authorize_live_or_historical_acceptance():
    import judgment_evaluation as evaluation
    with pytest.raises(ValueError, match='not complete'):
        evaluation.freeze(1, name='acceptance', mode='live')
    with pytest.raises(ValueError, match='Invalid comparison plan'):
        evaluation.freeze(1, name='judgment-acceptance-v3')


@pytest.mark.asyncio
async def test_failed_heldout_retains_denominators_and_rejects_label_or_trial_contamination(tmp_path, monkeypatch):
    from copy import deepcopy
    import judgment_evaluation as evaluation
    plan = evaluation.freeze(1, name='acceptance')
    monkeypatch.setattr(evaluation.legacy, 'save', lambda *args: None)
    async def failed(*args):
        raise RuntimeError('synthetic failure')
    artifact = await evaluation.run(plan, tmp_path / 'heldout.json', failed)
    summary = evaluation.summarize(plan, artifact)
    assert sum(g['counts']['planned'] for g in summary['groups'].values()) == 800
    assert all(g['counts']['problem'] == 150 and g['counts']['natural'] == 50 for g in summary['groups'].values())
    assert not summary['criteria_met'] and not summary['quality_accepted']
    changed = deepcopy(plan)
    changed['sets'][0]['cases'][0]['kind'] = 'natural'
    with pytest.raises(ValueError, match='Comparison inputs changed'):
        evaluation.audit(changed, artifact)
    changed = deepcopy(artifact)
    changed['trials'][0]['example_id'] = 'judgment-v2-calibration-01'
    with pytest.raises(ValueError, match='Missing, duplicate or moved trials'):
        evaluation.audit(plan, changed)
