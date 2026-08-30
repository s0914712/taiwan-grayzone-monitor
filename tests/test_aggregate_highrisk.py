"""高風險船週/月彙整 — aggregate_highrisk.py（純函式，不碰檔案/網路）"""
from datetime import date

import pytest

import aggregate_highrisk as agg


# ══════════════════════════════════════════════════════════════════
# 每日選船
# ══════════════════════════════════════════════════════════════════

def _row(mmsi, score=8, level='high', cable=False, offshore=False, **kw):
    r = {'mmsi': mmsi, 'risk_score': score, 'risk_level': level,
         'cable_loitering': cable, 'offshore_loitering': offshore,
         'loiter_h': 0, 'loiter_kn': None, 'ev': [], 'cables': [],
         'off_days': 0, 'non_top10_flag': False, 'sanctioned': False}
    r.update(kw)
    return r


def test_select_daily_priority_order():
    rows = [
        _row('1', score=9),                                # high
        _row('2', score=20, level='critical'),             # critical 最優先
        _row('3', score=10, cable=True),                   # 海纜滯留其次
        _row('4', score=15, offshore=True),                # 離岸滯留第三
        _row('5', score=14),                               # 之後看分數
    ]
    picked = agg.select_daily_highrisk(rows, max_rows=3)
    assert [r['mmsi'] for r in picked] == ['2', '3', '4']


def test_select_daily_cap_and_stability():
    rows = [_row(str(i), score=8) for i in range(10)]
    picked = agg.select_daily_highrisk(rows, max_rows=4)
    assert [r['mmsi'] for r in picked] == ['0', '1', '2', '3']  # 同分依 MMSI 穩定


# ══════════════════════════════════════════════════════════════════
# 航程切分
# ══════════════════════════════════════════════════════════════════

PORT_A = (24.0, 120.0)
PORT_B = (25.0, 121.0)


def fake_port_lookup(lat, lon):
    if abs(lat - PORT_A[0]) < 0.05 and abs(lon - PORT_A[1]) < 0.05:
        return '測試港A'
    if abs(lat - PORT_B[0]) < 0.05 and abs(lon - PORT_B[1]) < 0.05:
        return '測試港B'
    return None


def _pt(day, hour, lat, lon):
    return {'t': f'2026-07-{day:02d}T{hour:02d}:00:00+00:00',
            'lat': lat, 'lon': lon}


def test_segment_voyages_port_to_sea():
    """港A 出港後在海上 → 出港=港A、進港 None、海上時間=尾段跨度。"""
    pts = [_pt(1, 0, *PORT_A), _pt(1, 2, *PORT_A),
           _pt(1, 4, 24.5, 120.5), _pt(1, 6, 24.6, 120.6),
           _pt(1, 8, 24.7, 120.7)]
    v = agg.segment_voyages(pts, fake_port_lookup)
    assert v['departure_port'] == '測試港A'
    assert v['departure_time'] == '2026-07-01T02:00:00+00:00'
    assert v['arrival_port'] is None
    assert v['at_sea_hours'] == 4.0          # 04:00 → 08:00
    assert v['observed_span_only'] is False


def test_segment_voyages_port_to_port():
    """港A → 海上 → 港B：完整航程。"""
    pts = [_pt(1, 0, *PORT_A),
           _pt(1, 2, 24.5, 120.5), _pt(1, 4, 24.8, 120.8),
           _pt(1, 6, *PORT_B), _pt(1, 8, *PORT_B)]
    v = agg.segment_voyages(pts, fake_port_lookup)
    assert v['departure_port'] == '測試港A'
    assert v['arrival_port'] == '測試港B'
    assert v['arrival_time'] == '2026-07-01T06:00:00+00:00'
    assert v['at_sea_hours'] == 2.0          # 02:00 → 04:00
    assert v['observed_span_only'] is False


def test_segment_voyages_never_in_port():
    """全程無港（如上海出發 — CN_PORTS 不涵蓋）→ observed_span_only。"""
    pts = [_pt(1, 0, 26.0, 122.0), _pt(1, 6, 26.1, 122.1),
           _pt(1, 12, 26.2, 122.2)]
    v = agg.segment_voyages(pts, fake_port_lookup)
    assert v['departure_port'] is None
    assert v['arrival_port'] is None
    assert v['at_sea_hours'] == 12.0
    assert v['observed_span_only'] is True


def test_segment_voyages_unsorted_and_bad_timestamps():
    """亂序輸入照 t 排序；壞時間戳/缺座標點被忽略。"""
    pts = [_pt(1, 4, 24.5, 120.5),
           _pt(1, 0, *PORT_A),
           {'t': 'garbage', 'lat': 24.9, 'lon': 120.9},
           {'t': '2026-07-01T02:00:00+00:00', 'lat': None, 'lon': 120.0},
           _pt(1, 2, *PORT_A)]
    v = agg.segment_voyages(pts, fake_port_lookup)
    assert v['departure_port'] == '測試港A'
    assert v['departure_time'] == '2026-07-01T02:00:00+00:00'


def test_segment_voyages_empty():
    v = agg.segment_voyages([], fake_port_lookup)
    assert v == agg.empty_voyage()


def test_segment_voyages_all_in_port():
    pts = [_pt(1, 0, *PORT_A), _pt(1, 2, *PORT_A)]
    v = agg.segment_voyages(pts, fake_port_lookup)
    assert v['arrival_port'] == '測試港A'
    assert v['at_sea_hours'] == 0.0
    assert v['departure_port'] is None


# ══════════════════════════════════════════════════════════════════
# 累積檔 merge
# ══════════════════════════════════════════════════════════════════

def _meta(name='SHIP', vtype='cargo'):
    return {'name': name, 'type': vtype, 'last_lat': 24.0, 'last_lon': 121.0,
            'last_seen': '2026-07-01T00:00:00+00:00', 'zone': 'eez',
            'voyage': agg.empty_voyage()}


def test_merge_creates_and_max_merges():
    acc = agg.new_accumulator()
    r1 = _row('412', score=9, loiter_h=3.0, loiter_kn=2.5,
              ev=[[24.1, 121.1, 3.0, 2.5, '2026-07-01']], cables=['a'])
    agg.merge_into_accumulator(acc, '2026-07-01', [r1], {'412': _meta()},
                               now_iso='x')
    # 同日第二輪：分數更高、滯留更久、同一事件長大 + 新事件
    r2 = _row('412', score=12, level='critical', loiter_h=5.0, loiter_kn=1.5,
              ev=[[24.1, 121.1, 5.0, 1.5, '2026-07-01'],
                  [24.5, 121.5, 3.0, 2.0, '2026-07-01']],
              cables=['b'], off_days=6.0)
    agg.merge_into_accumulator(acc, '2026-07-01', [r2], {}, now_iso='x')
    rec = acc['daily']['2026-07-01']['412']
    assert rec['s'] == 12 and rec['lv'] == 'critical'
    assert rec['lh'] == 5.0 and rec['lk'] == 1.5
    assert rec['od'] == 6.0
    assert rec['cb'] == ['a', 'b']
    assert len(rec['ev']) == 2               # 同鍵事件取時數較大者
    assert [24.1, 121.1, 5.0, 1.5, '2026-07-01'] in rec['ev']


def test_merge_idempotent():
    import copy
    acc = agg.new_accumulator()
    r = _row('412', score=9, ev=[[24.1, 121.1, 3.0, 2.5, '2026-07-01']])
    agg.merge_into_accumulator(acc, '2026-07-01', [r], {'412': _meta()},
                               now_iso='x')
    once = copy.deepcopy(acc['daily'])
    agg.merge_into_accumulator(acc, '2026-07-01', [r], {'412': _meta()},
                               now_iso='x')
    assert acc['daily'] == once


def test_merge_retention_trim_and_vessel_prune():
    acc = agg.new_accumulator()
    agg.merge_into_accumulator(acc, '2026-01-01', [_row('OLD')],
                               {'OLD': _meta('OLD')}, retention_days=3,
                               now_iso='x')
    for d in ('2026-07-01', '2026-07-02', '2026-07-03'):
        agg.merge_into_accumulator(acc, d, [_row('NEW')],
                                   {'NEW': _meta('NEW')}, retention_days=3,
                                   now_iso='x')
    assert '2026-01-01' not in acc['daily']
    assert len(acc['daily']) == 3
    assert 'OLD' not in acc['vessels']       # 沒有日列的 meta 剪除
    assert 'NEW' in acc['vessels']


def test_merge_sorted_keys_for_stable_git_delta():
    acc = agg.new_accumulator()
    agg.merge_into_accumulator(acc, '2026-07-02', [_row('B'), _row('A')],
                               {'B': _meta(), 'A': _meta()}, now_iso='x')
    agg.merge_into_accumulator(acc, '2026-07-01', [_row('C')],
                               {'C': _meta()}, now_iso='x')
    assert list(acc['daily']) == ['2026-07-01', '2026-07-02']
    assert list(acc['daily']['2026-07-02']) == ['A', 'B']
    assert list(acc['vessels']) == ['A', 'B', 'C']


# ══════════════════════════════════════════════════════════════════
# 區間彙整 + 報表
# ══════════════════════════════════════════════════════════════════

def _acc_with_week():
    """7/06(一)–7/12(日) 的一週資料 + 週外一天。"""
    acc = agg.new_accumulator()
    # 同一事件連兩天出現（14 天視窗殘影）→ 彙整只能算一次
    ev = [[24.1, 121.1, 4.0, 2.0, '2026-07-06']]
    agg.merge_into_accumulator(
        acc, '2026-07-06',
        [_row('412111111', score=12, level='critical', loiter_h=4.0,
              loiter_kn=2.0, ev=ev, cables=['apcn-2']),
         _row('412222222', score=8)],
        {'412111111': _meta('LOITERER', 'tanker'),
         '412222222': _meta('FISHER', 'fishing')}, now_iso='x')
    agg.merge_into_accumulator(
        acc, '2026-07-07',
        [_row('412111111', score=9, loiter_h=4.0, loiter_kn=2.0, ev=ev),
         _row('351333333', score=10, non_top10_flag=True,
              ev=[[25.0, 122.0, 3.0, 1.0, '2026-07-07']])],
        {'351333333': _meta('FOC SHIP', 'cargo')}, now_iso='x')
    # 週外（前一週日）
    agg.merge_into_accumulator(acc, '2026-07-05', [_row('999888777')],
                               {'999888777': _meta('OUTSIDE')}, now_iso='x')
    return acc


def test_bucket_range_dedupes_events_and_filters_window():
    acc = _acc_with_week()
    vessels, daily_counts, days = agg.bucket_range(
        acc, '2026-07-06', '2026-07-12')
    assert set(days) == {'2026-07-06', '2026-07-07'}
    assert '999888777' not in vessels
    v = vessels['412111111']
    assert v['days_seen'] == 2
    assert v['max_score'] == 12 and v['risk_level'] == 'critical'
    assert len(v['events']) == 1             # 兩天同一事件 → 去重為一
    assert daily_counts == {'2026-07-06': 2, '2026-07-07': 2}


def test_bucket_range_drops_event_started_before_window():
    acc = agg.new_accumulator()
    agg.merge_into_accumulator(
        acc, '2026-07-06',
        [_row('412', score=9, ev=[[24.0, 121.0, 5.0, 2.0, '2026-06-28']])],
        {'412': _meta()}, now_iso='x')
    vessels, _, _ = agg.bucket_range(acc, '2026-07-06', '2026-07-12')
    assert vessels['412']['events'] == {}    # 事件起始日在區間外


def test_build_period_report_summary_and_csv_rows():
    acc = _acc_with_week()
    mid_table = {'412': {'en': 'China', 'zh': '中國'},
                 '351': {'en': 'Panama', 'zh': '巴拿馬'}}
    report = agg.build_period_report(
        acc, '2026-07-06', '2026-07-12', mid_table,
        {'period': 'weekly', 'week': '2026-W28'})
    s = report['summary']
    assert s['unique_highrisk'] == 3
    assert s['critical'] == 1
    assert s['cable_loiter_vessels'] == 2    # 412111111 + 351333333
    assert s['cable_loiter_hours_total'] == 7.0   # 4.0 + 3.0（去重後）
    assert s['by_type'] == {'tanker': 1, 'fishing': 1, 'cargo': 1}
    assert s['by_flag']['412']['count'] == 2
    assert s['by_flag']['351']['zh'] == '巴拿馬'
    assert report['days_covered'] == 2
    assert report['week'] == '2026-W28'

    rows = report['vessels']
    assert rows[0]['mmsi'] == '412111111'    # 最高分在前
    assert rows[0]['cable_loiter_hours'] == 4.0
    assert rows[0]['cable_loiter_avg_speed_kn'] == 2.0
    assert rows[0]['flag_zh'] == '中國'
    assert rows[0]['gov_category'] == ''
    assert rows[0]['time_at_sea_note'] == '未觀測到靠港'  # empty_voyage
    assert report['hotspots'][0]['vessels'] >= 1


def test_build_period_report_empty_window():
    report = agg.build_period_report(
        agg.new_accumulator(), '2026-07-06', '2026-07-12', {},
        {'period': 'weekly', 'week': '2026-W28'})
    assert report['summary']['unique_highrisk'] == 0
    assert report['vessels'] == []
    assert report['hotspots'] == []
    assert report['days_covered'] == 0


# ══════════════════════════════════════════════════════════════════
# 期間計算 + gate
# ══════════════════════════════════════════════════════════════════

def test_previous_iso_week():
    label, start, end = agg.previous_iso_week(date(2026, 8, 31))  # 週一
    assert (label, start, end) == ('2026-W35',
                                   date(2026, 8, 24), date(2026, 8, 30))
    # 跨年：2027-01-01 是週五 → 上一完整週是 2026-W52（12/21–12/27）
    label, start, end = agg.previous_iso_week(date(2027, 1, 1))
    assert start.weekday() == 0 and (end - start).days == 6
    assert end < date(2027, 1, 1)


def test_previous_month():
    assert agg.previous_month(date(2026, 9, 1)) == (
        '2026-08', date(2026, 8, 1), date(2026, 8, 31))
    assert agg.previous_month(date(2026, 1, 3)) == (
        '2025-12', date(2025, 12, 1), date(2025, 12, 31))


def test_iso_weeks_in_range_clamped_to_month():
    weeks = agg.iso_weeks_in_range(date(2026, 8, 1), date(2026, 8, 31))
    assert weeks[0][1] == date(2026, 8, 1)       # 首週夾在月首
    assert weeks[-1][2] == date(2026, 8, 31)     # 尾週夾在月末
    assert all(w[1] <= w[2] for w in weeks)


def test_weekly_gate(tmp_path):
    out = tmp_path / '2026-W35.json'
    assert agg.should_run_weekly(date(2026, 8, 31), out) is True    # 週一
    assert agg.should_run_weekly(date(2026, 9, 2), out) is True     # 週三
    assert agg.should_run_weekly(date(2026, 9, 3), out) is False    # 週四
    out.write_text('{}')
    assert agg.should_run_weekly(date(2026, 8, 31), out) is False   # 已存在
    assert agg.should_run_weekly(date(2026, 9, 3), out, force=True) is True


def test_monthly_gate(tmp_path):
    out = tmp_path / '2026-08.json'
    assert agg.should_run_monthly(date(2026, 9, 1), out) is True
    assert agg.should_run_monthly(date(2026, 9, 3), out) is True
    assert agg.should_run_monthly(date(2026, 9, 4), out) is False
    out.write_text('{}')
    assert agg.should_run_monthly(date(2026, 9, 1), out) is False
    assert agg.should_run_monthly(date(2026, 9, 9), out, force=True) is True


# ══════════════════════════════════════════════════════════════════
# CSV
# ══════════════════════════════════════════════════════════════════

def test_write_report_csv_utf8_sig_and_columns(tmp_path):
    import csv
    acc = _acc_with_week()
    report = agg.build_period_report(
        acc, '2026-07-06', '2026-07-12',
        {'412': {'en': 'China', 'zh': '中國'}}, {'period': 'weekly'})
    path = tmp_path / 'w.csv'
    agg.write_report_csv(path, report['vessels'])
    raw = path.read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf')   # BOM → Excel 中文不亂碼
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == agg.CSV_COLUMNS
    assert len(rows) == 3
    assert rows[0]['flag_zh'] == '中國'
    assert rows[0]['non_top10_flag'] in ('0', '1')


def test_write_report_csv_empty_has_header(tmp_path):
    import csv
    path = tmp_path / 'empty.csv'
    agg.write_report_csv(path, [])
    with open(path, encoding='utf-8-sig', newline='') as f:
        r = csv.reader(f)
        header = next(r)
        assert header == agg.CSV_COLUMNS
        assert list(r) == []


# ══════════════════════════════════════════════════════════════════
# 前端 manifest
# ══════════════════════════════════════════════════════════════════

def test_build_manifest_entry():
    report = {'week': '2026-W35', 'start': '2026-08-24', 'end': '2026-08-30',
              'days_covered': 7, 'generated_at': 'x',
              'summary': {'unique_highrisk': 400, 'critical': 113,
                          'cable_loiter_vessels': 283,
                          'cable_loiter_hours_total': 2024.4}}
    e = agg.build_manifest_entry(report)
    assert e['week'] == '2026-W35'
    assert e['unique_highrisk'] == 400
    assert 'month' not in e


def test_write_manifest_scans_dirs_newest_first(tmp_path):
    import json
    wd = tmp_path / 'weekly'
    md = tmp_path / 'monthly'
    wd.mkdir()
    md.mkdir()
    for label in ('2026-W34', '2026-W35'):
        (wd / f'{label}.json').write_text(json.dumps(
            {'week': label, 'summary': {'unique_highrisk': 1}}))
    (wd / f'{label}.csv').write_text('x')          # 非 json 不干擾
    (md / '2026-08.json').write_text(json.dumps(
        {'month': '2026-08', 'summary': {}}))
    m = agg.write_manifest(weekly_dir=wd, monthly_dir=md)
    assert [e['week'] for e in m['weekly']] == ['2026-W35', '2026-W34']
    assert m['monthly'][0]['month'] == '2026-08'
    # 自己的 index.json 不會被掃回去（重跑冪等）
    m2 = agg.write_manifest(weekly_dir=wd, monthly_dir=md)
    assert len(m2['weekly']) == 2
