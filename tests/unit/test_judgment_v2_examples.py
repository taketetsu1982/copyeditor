"""New synthetic calibration drafts; owner/native review are pending."""
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
    'judgment-v2-calibration-11': {'axis': 'stiffness', 'topic': 'canal-lock-viewing', 'origin': 'synthetic-v2/canal-lock-viewing', 'anchors': ['予約制', '10分前', '管理棟'], 'witness': '非常に大切です', 'invariant': 'Keep booking, arrival time and destination; add no late-entry policy.'},
    'judgment-v2-calibration-12': {'axis': 'stiffness', 'topic': 'bus-transfer-display', 'origin': 'synthetic-v2/bus-transfer-display', 'anchors': ['発車時刻と乗り場', '待ち時間の見通し'], 'witness': '乗換納得', 'invariant': 'Keep departure time, stop and waiting-time information; do not guarantee a connection.'},
    'judgment-v2-calibration-13': {'axis': 'abstraction', 'topic': 'textile-dye-swatch', 'origin': 'synthetic-v2/textile-dye-swatch', 'anchors': ['同じ布', '濃さの違う藍液'], 'witness': '可能性を最大化', 'invariant': 'Keep the same fabric and different dye concentrations; do not promise an exact final color.'},
    'judgment-v2-calibration-14': {'axis': 'abstraction', 'topic': 'community-radio-request', 'origin': 'synthetic-v2/community-radio-request', 'anchors': ['曲名', '放送してほしい理由'], 'witness': '共感の輪を広げる架け橋', 'invariant': 'Keep both required fields and the request; do not promise that every song will be broadcast.'},
    'judgment-v2-calibration-15': {'axis': 'formulaic', 'topic': 'bird-ring-report', 'origin': 'synthetic-v2/bird-ring-report', 'anchors': ['読める範囲', '番号と観察場所', '鳥を追いかけない'], 'witness': 'ぜひ参考にしてみてください', 'invariant': 'Keep the limited observation request and prohibition; do not add handling advice.'},
    'judgment-v2-calibration-16': {'axis': 'formulaic', 'topic': 'tram-model-preorder', 'origin': 'synthetic-v2/tram-model-preorder', 'anchors': ['予約した色で用意します', '店頭のみ', '発送はできません'], 'witness': 'いかがでしたか', 'invariant': 'Keep the color commitment and store-only pickup; add no delivery date or stock promise.'},
    'judgment-v2-calibration-17': {'axis': 'roundabout', 'topic': 'archive-reading-gloves', 'origin': 'synthetic-v2/archive-reading-gloves', 'anchors': ['受付で渡す手袋', '写真の裏面を見る場合も'], 'witness': 'と言えるでしょう', 'invariant': 'Keep glove use on both sides and the provided gloves; preserve the polite instruction.'},
    'judgment-v2-calibration-18': {'axis': 'roundabout', 'topic': 'waterwheel-maintenance-day', 'origin': 'synthetic-v2/waterwheel-maintenance-day', 'anchors': ['毎月最初の火曜日', '点検中は水車を動か'], 'witness': 'お伝えしておきたいと思います', 'invariant': 'Keep the recurring date and stopped-operation condition; do not imply the entire exhibition closes.'},
    'judgment-v2-calibration-19': {'axis': 'repetition', 'topic': 'pottery-tool-return', 'origin': 'synthetic-v2/pottery-tool-return', 'anchors': ['番号の付いた棚', '欠けた道具は棚に戻さず受付へ'], 'witness': 'さらに、', 'invariant': 'Keep washing, drying and return order with the damaged-tool exception.'},
    'judgment-v2-calibration-20': {'axis': 'repetition', 'topic': 'island-ferry-weather-board', 'origin': 'synthetic-v2/island-ferry-weather-board', 'anchors': ['港の掲示板', '天候が変わると', '当日の運航を保証しません'], 'witness': 'そして、', 'invariant': 'Keep status, cancellation reason, weather-driven updates and the explicit lack of a next-day guarantee.'},
    'judgment-v2-calibration-21': {'axis': 'natural', 'control': 'technical', 'topic': 'eyepiece-case-label', 'origin': 'synthetic-v2/eyepiece-case-label', 'anchors': ['接眼レンズ', '焦点距離', '見掛け視界'], 'witness': '焦点距離', 'invariant': 'Keep the optical terms, label fields and visibility without removing the lens; keep every character unchanged.'},
    'judgment-v2-calibration-22': {'axis': 'natural', 'control': 'repetition', 'topic': 'tactile-station-map', 'origin': 'synthetic-v2/tactile-station-map', 'anchors': ['触知図', '改札の外', '案内係'], 'witness': '触知図', 'invariant': 'Keep repeated map references, location and conditional assistance; keep every character unchanged.'},
    'judgment-v2-calibration-23': {'axis': 'natural', 'control': 'technical', 'topic': 'warp-thread-sample', 'origin': 'synthetic-v2/warp-thread-sample', 'anchors': ['経糸', '緯糸', '台紙の矢印'], 'witness': '経糸', 'invariant': 'Keep warp/weft materials and directional arrows; keep every character unchanged.'},
    'judgment-v2-calibration-24': {'axis': 'natural', 'control': 'condition', 'topic': 'cat-adoption-visit', 'origin': 'synthetic-v2/cat-adoption-visit', 'anchors': ['予約が必要', '当日に引き取ることはできません', '飼育環境'], 'witness': '引き取りの日', 'invariant': 'Keep the booking requirement, same-day prohibition and order of consultation; keep every character unchanged.'},
    'judgment-v2-calibration-25': {'axis': 'natural', 'control': 'repetition', 'topic': 'ensemble-rest-count', 'origin': 'synthetic-v2/ensemble-rest-count', 'anchors': ['休符', '指揮者の合図', '先に入らない'], 'witness': '休符', 'invariant': 'Keep each distinct rest instruction and the prohibition; keep every character unchanged.'},
    'judgment-v2-calibration-26': {'axis': 'natural', 'control': 'technical', 'topic': 'snow-depth-observation', 'origin': 'synthetic-v2/snow-depth-observation', 'anchors': ['積雪深', '毎朝8時', '欠測'], 'witness': '欠測', 'invariant': 'Keep the measurement method, time and missing-data condition; keep every character unchanged.'},
    'judgment-v2-calibration-27': {'axis': 'natural', 'control': 'repetition', 'topic': 'resident-mailbox-key', 'origin': 'synthetic-v2/resident-mailbox-key', 'anchors': ['各戸に1本', '本人確認後', '予備の鍵'], 'witness': '管理室', 'invariant': 'Keep distribution, reporting and identity-check condition; keep every character unchanged.'},
    'judgment-v2-calibration-28': {'axis': 'natural', 'control': 'technical', 'topic': 'spectral-csv-export', 'origin': 'synthetic-v2/spectral-csv-export', 'anchors': ['CSV', '波長', '強度', 'nm'], 'witness': '強度', 'invariant': 'Keep file format, column order and unit locations; keep every character unchanged.'},
    'judgment-v2-calibration-29': {'axis': 'natural', 'control': 'style', 'topic': 'exhibition-memory-diary', 'origin': 'synthetic-v2/exhibition-memory-diary', 'anchors': ['一場面', '作品の名前', '帰り道'], 'witness': '心に残った', 'invariant': 'Keep the optional invitation and permissive tone; the concrete memory metaphor is not a removable defect.'},
    'judgment-v2-calibration-30': {'axis': 'natural', 'control': 'style', 'topic': 'wooden-radio-cabinet', 'origin': 'synthetic-v2/wooden-radio-cabinet', 'anchors': ['木目', '音量つまみは前面', '電源端子は背面'], 'witness': '一台ずつの表情', 'invariant': 'Keep the restrained product metaphor and control locations; keep every character unchanged.'},
}

CASES_V2 = [c for c in CASES if c['id'].startswith('judgment-v2-calibration-')]


def test_new_calibration_subset_has_independent_topics_and_bodies():
    expected = [f'judgment-v2-calibration-{i:02}' for i in range(1, 31)]
    assert list(MANIFEST) == [c['id'] for c in CASES_V2] == expected
    assert Counter(m['axis'] for m in MANIFEST.values()) == dict(
        stiffness=4, abstraction=4, formulaic=4, roundabout=4, repetition=4, natural=10)
    old_topics = {m[1] for m in (CALIBRATION_MANIFEST | ACCEPTANCE_MANIFEST).values()}
    assert len({m['topic'] for m in MANIFEST.values()}) == len(expected)
    assert not {m['topic'] for m in MANIFEST.values()} & old_topics
    assert len({m['origin'] for m in MANIFEST.values()}) == len(expected)
    other = {c[k] for c in CASES if c not in CASES_V2 for k in ('bad', 'good')} | {r['text'] for r in REFERENCES}
    texts = [c[k] for c in CASES_V2 for k in ('bad', 'good')]
    assert len({c['bad'] for c in CASES_V2}) == len({c['good'] for c in CASES_V2}) == 30
    assert not set(texts) & other
    assert all(a not in b and b not in a for a in texts for b in other)


@pytest.mark.parametrize('case', CASES_V2, ids=lambda c: c['id'])
def test_new_calibration_drafts_remove_worst_expression_and_keep_constraints(case):
    draft = MANIFEST[case['id']]
    natural = draft['axis'] == 'natural'
    assert case['must_change'] is not natural
    assert (case['bad'] == case['good']) is natural
    assert draft['witness'] in case['bad']
    assert (draft['witness'] in case['good']) is natural
    assert all(a in case['bad'] and a in case['good'] for a in draft['anchors'])
    assert draft['invariant'] and draft['origin'] == 'synthetic-v2/' + draft['topic']
    assert case['background'] == {} and case['degree'] == 'polish' and case['format'] == 'text'
    config, snapshot = adapter.environment(case, 'fixture')
    ratio = {k: config['length_ratio.' + k] for k in ('min', 'max')}
    assert not check(case['bad'], case['good'], snapshot.languages['ja'].protected_terms, ratio).failed


def test_complete_calibration_freezes_all_trials_without_owner_acceptance():
    import judgment_evaluation as evaluation
    plan = evaluation.freeze(1, name='calibration')
    population = plan['sets'][0]
    assert population['name'] == 'calibration-v4'
    assert population['population_hash'] == evaluation.POPULATION_PINS['calibration'][2]
    assert population['owner_labels'] == population['native_review'] == 'pending'
    assert Counter(c['kind'] for c in population['cases']) == dict(problem=20, natural=10)
    assert all(c['origin'] == 'synthetic-v2:' + c['id'] and c['sha256'] for c in population['cases'])
    assert len(plan['planned_trials']) == 1200 and len(plan['request_layouts']) == 720
    assert Counter(t['layout'] for t in plan['planned_trials']) == dict(text=600, items=600)
    assert set(t['example_id'] for t in plan['planned_trials']) == set(MANIFEST)
    assert not plan['acceptance_criteria']['quality_accepted']
    assert len(evaluation.population('existing')) == 24
    assert Counter(m.get('control') for m in MANIFEST.values() if m['axis'] == 'natural') == dict(
        technical=4, repetition=3, condition=1, style=2)


@pytest.mark.parametrize('change', ['empty', 'missing', 'extra', 'changed', 'historical'])
def test_calibration_population_hash_rejects_missing_or_mixed_assets(tmp_path, monkeypatch, change):
    import shutil
    import judgment_evaluation as evaluation
    source = evaluation.legacy.ROOT
    target = tmp_path / 'examples/ja'
    target.mkdir(parents=True)
    if change != 'empty':
        for path in (source / 'examples/ja').glob('judgment-v2-calibration-*.yaml'):
            shutil.copyfile(path, target / path.name)
        first = target / 'judgment-v2-calibration-01.yaml'
        if change == 'missing': first.unlink()
        elif change == 'extra': shutil.copyfile(first, target / 'judgment-v2-calibration-99.yaml')
        elif change == 'changed': first.write_text(first.read_text() + '\n# changed\n')
        else: shutil.copyfile(source / 'examples/ja/judgment-calibration-01.yaml', first)
    monkeypatch.setattr(evaluation.legacy, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='Missing or changed evaluation population'):
        evaluation.population('calibration')


def test_complete_assets_do_not_authorize_live_or_historical_calibration():
    import judgment_evaluation as evaluation
    with pytest.raises(ValueError, match='not complete'):
        evaluation.freeze(1, name='calibration', mode='live')
    with pytest.raises(ValueError, match='Invalid comparison plan'):
        evaluation.freeze(1, name='calibration-v3')


@pytest.mark.asyncio
async def test_failed_calibration_keeps_new_population_denominators(tmp_path, monkeypatch):
    import judgment_evaluation as evaluation
    plan = evaluation.freeze(1, name='calibration')
    monkeypatch.setattr(evaluation.legacy, 'save', lambda *args: None)
    async def failed(*args):
        raise RuntimeError('synthetic failure')
    artifact = await evaluation.run(plan, tmp_path / 'calibration.json', failed)
    assert len(evaluation.audit(plan, artifact)) == 720
    summary = evaluation.summarize(plan, artifact)
    assert sum(g['counts']['planned'] for g in summary['groups'].values()) == 1200
    assert all(g['counts']['problem'] == 100 and g['counts']['natural'] == 50 for g in summary['groups'].values())
    assert not summary['criteria_met'] and not summary['quality_accepted']
