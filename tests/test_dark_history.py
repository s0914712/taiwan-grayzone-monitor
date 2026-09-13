"""暗船每日歷史：GFW 回溯配對修訂的偵測。

GFW 的 AIS 配對是回溯補的，30 天滾動視窗會把同一個偵測日重查約 30 次。
先前的寫法用最新值覆蓋、修訂被靜默蓋掉，於是「暗船比例 40.5%」這種很可能
只是配對還沒跑完的數字，在頁面上跟真實的長期基線（18~20%）看起來一樣可信。
"""

import generate_dashboard as gd


def test_first_observation_is_preserved_across_revisions():
    history = {}
    gd.merge_dark_history(history, {'2026-09-09': 100}, {'2026-09-09': 250},
                          '2026-09-09')
    # 幾天後 GFW 補完配對，暗船數下修
    gd.merge_dark_history(history, {'2026-09-09': 60}, {'2026-09-09': 250},
                          '2026-09-12')

    e = history['2026-09-09']
    assert e['first_dark'] == 100 and e['first_total'] == 250
    assert e['dark_vessels'] == 60          # 最新值仍是權威值
    assert e['first_seen'] == '2026-09-09' and e['last_seen'] == '2026-09-12'
    assert e['revisions'] == 1


def test_unchanged_revalidation_is_not_counted_as_a_revision():
    history = {}
    for day in ('2026-09-09', '2026-09-10', '2026-09-11'):
        gd.merge_dark_history(history, {'2026-09-09': 40}, {'2026-09-09': 100}, day)
    assert history['2026-09-09']['revisions'] == 0
    assert history['2026-09-09']['last_seen'] == '2026-09-11'


def test_days_with_detections_but_no_dark_vessels_are_recorded():
    # dark_by_date 不含零暗船的日子；只看分子會讓比例趨勢偏高
    history = {}
    gd.merge_dark_history(history, {}, {'2026-09-07': 30}, '2026-09-07')
    assert history['2026-09-07']['dark_vessels'] == 0
    assert history['2026-09-07']['total_detections'] == 30


def test_legacy_entries_are_adopted_without_a_fake_denominator():
    # 舊格式的 total_detections 是「沿用暗船數」的假分母，不能拿來算比例
    history = {'2026-08-01': {'dark_vessels': 500, 'total_detections': 500}}
    gd.merge_dark_history(history, {'2026-08-01': 400}, {'2026-08-01': 1200},
                          '2026-08-20')
    e = history['2026-08-01']
    assert e['first_dark'] == 500
    assert 'first_total' not in e            # 假分母不回填
    assert e['total_detections'] == 1200 and e['total_is_measured'] is True
    assert e['revisions'] == 1


def test_missing_totals_keep_the_old_convention_and_no_ratio():
    history = {}
    gd.merge_dark_history(history, {'2026-08-01': 10}, {}, '2026-08-01')
    e = history['2026-08-01']
    assert e['total_detections'] == 10
    assert 'total_is_measured' not in e


def test_revision_summary_measures_backfill_downgrade():
    history = {}
    gd.merge_dark_history(history, {'d1': 100, 'd2': 100}, {'d1': 200, 'd2': 200},
                          '2026-09-01')
    gd.merge_dark_history(history, {'d1': 50, 'd2': 100}, {'d1': 200, 'd2': 200},
                          '2026-09-10')

    s = gd.summarize_dark_revision(history)
    assert s['dates_tracked'] == 2
    assert s['dates_revised'] == 1 and s['dates_revised_down'] == 1
    assert s['first_dark_total'] == 200 and s['latest_dark_total'] == 150
    assert s['dark_delta_pct'] == -25.0
    # 初次公布 50%，回溯補完後其實是 37.5%
    assert s['first_ratio_pct'] == 50.0
    assert s['latest_ratio_pct'] == 37.5
    assert s['ratio_delta_pts'] == -12.5
    assert s['ratio_dates'] == 2


def test_revision_summary_ignores_legacy_only_history():
    history = {'2026-08-01': {'dark_vessels': 500, 'total_detections': 500}}
    s = gd.summarize_dark_revision(history)
    assert s['dates_tracked'] == 0
    assert s['dark_delta_pct'] == 0.0
    assert 'first_ratio_pct' not in s        # 沒有可信分母就不報比例


def test_refresh_writes_revision_into_the_dark_vessels_block(tmp_path,
                                                             monkeypatch):
    monkeypatch.setattr(gd, 'DARK_HISTORY_PATH', tmp_path / 'hist.json')
    dark = {'overall': {'dark_by_date': {'2026-09-09': 40},
                        'total_by_date': {'2026-09-09': 100}}}
    vessel = gd.refresh_vessel_monitoring_daily({}, dark)

    assert dark['revision']['dates_tracked'] == 1
    assert vessel['daily'] == [{'date': '2026-09-09', 'dark_vessels': 40,
                                'total_detections': 100, 'dark_ratio': 40.0}]
    assert vessel['summary']['avg_daily_detections'] == 100
    assert vessel['summary']['measured_total_days'] == 1
