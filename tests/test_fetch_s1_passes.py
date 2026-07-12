"""Tests for fetch_s1_passes.py — CMR granule → Sentinel-1 pass-table parsing,
and the pass-table override in match_sar_ais.pass_candidates."""

import fetch_s1_passes as fp
import match_sar_ais as m


def granule(begin, direction=None, platform='SENTINEL-1A',
            ur='S1A_IW_GRDH_1SDV_20260708T095123'):
    umm = {
        'TemporalExtent': {'RangeDateTime': {'BeginningDateTime': begin}},
        'Platforms': [{'ShortName': platform}],
        'GranuleUR': ur,
    }
    if direction:
        umm['AdditionalAttributes'] = [
            {'Name': 'ASCENDING_DESCENDING', 'Values': [direction]},
        ]
    return {'umm': umm}


# ── granule 欄位解析 ─────────────────────────────────────────────────────────

def test_granule_fields_parsed():
    dt, direction, plat, ur = fp._granule_fields(
        granule('2026-07-08T09:51:23.000Z', 'ASCENDING'))
    assert dt.strftime('%Y-%m-%d %H:%M:%S') == '2026-07-08 09:51:23'
    assert direction == 'ascending'
    assert plat == 'S1A'


def test_direction_inferred_from_hour_when_attribute_missing():
    dt, direction, _, _ = fp._granule_fields(granule('2026-07-08T21:54:00Z'))
    assert direction == 'descending'
    dt, direction, _, _ = fp._granule_fields(granule('2026-07-08T09:50:00Z'))
    assert direction == 'ascending'


def test_invalid_granule_returns_none():
    assert fp._granule_fields({'umm': {}}) is None
    assert fp._granule_fields(
        {'umm': {'TemporalExtent': {'RangeDateTime':
                                    {'BeginningDateTime': 'not-a-date'}}}}) is None


def test_iw_grd_filter():
    assert fp.is_iw_grd('S1A_IW_GRDH_1SDV_20260708T095123')
    assert not fp.is_iw_grd('S1A_IW_SLC__1SDV_20260708T095123')
    assert not fp.is_iw_grd('S1A_IW_RAW__0SDV_20260708T095123')
    assert not fp.is_iw_grd('S1A_IW_OCN__2SDV_20260708T095123')
    assert not fp.is_iw_grd('S1A_EW_GRDM_1SDH_20260708T095123')
    assert fp.is_iw_grd('')            # 命名不明時保守保留
    assert fp.is_iw_grd('SOME-OTHER-NAMING')


# ── 過境聚類 ─────────────────────────────────────────────────────────────────

def test_consecutive_frames_cluster_into_one_pass():
    items = [
        granule('2026-07-08T09:51:00Z', 'ASCENDING'),
        granule('2026-07-08T09:53:30Z', 'ASCENDING'),   # 同過境下一 frame
        granule('2026-07-08T09:56:00Z', 'ASCENDING'),
        granule('2026-07-08T21:54:00Z', 'DESCENDING'),  # 晚間另一次過境
    ]
    passes = fp.build_pass_table(fp.cmr_records(items))
    assert list(passes) == ['2026-07-08']
    day = passes['2026-07-08']
    assert len(day) == 2
    asc = next(p for p in day if p['direction'] == 'ascending')
    assert asc['time'] == '09:51:00'
    assert asc['frames'] == 3


def test_two_platforms_same_direction_are_separate_passes():
    items = [
        granule('2026-07-08T09:51:00Z', 'ASCENDING', 'SENTINEL-1A'),
        granule('2026-07-08T10:03:00Z', 'ASCENDING', 'SENTINEL-1C'),
    ]
    passes = fp.build_pass_table(fp.cmr_records(items))
    day = passes['2026-07-08']
    assert len(day) == 2
    assert {p['platform'] for p in day} == {'S1A', 'S1C'}


def test_gap_beyond_threshold_splits_pass():
    items = [
        granule('2026-07-08T09:51:00Z', 'ASCENDING'),
        granule('2026-07-08T11:00:00Z', 'ASCENDING'),   # 69 分鐘後 → 另一次
    ]
    passes = fp.build_pass_table(fp.cmr_records(items))
    assert len(passes['2026-07-08']) == 2


# ── CDSE OData 備援 ──────────────────────────────────────────────────────────

def test_parse_s1_name():
    plat, dt = fp._parse_s1_name(
        'S1A_IW_GRDH_1SDV_20260708T095123_20260708T095148_059123_0756AB_4C26')
    assert plat == 'S1A'
    assert dt.strftime('%Y-%m-%d %H:%M:%S') == '2026-07-08 09:51:23'
    assert fp._parse_s1_name('') is None
    assert fp._parse_s1_name('S2A_MSIL2A_20260708T021341') is None
    assert fp._parse_s1_name('S1A_IW_GRDH_1SDV_notadate_x_y') is None


def test_cdse_records_and_pass_table():
    items = [
        {'Name': 'S1A_IW_GRDH_1SDV_20260708T095123_20260708T095148_059123_0756AB_4C26'},
        {'Name': 'S1A_IW_GRDH_1SDV_20260708T095148_20260708T095213_059123_0756AB_9F01'},
        {'Name': 'S1C_IW_GRDH_1SDV_20260708T215402_20260708T215427_003210_00512F_AA11'},
        {'Name': 'garbage'},
    ]
    recs = fp.cdse_records(items)
    assert len(recs) == 3
    passes = fp.build_pass_table(recs)
    day = passes['2026-07-08']
    assert len(day) == 2
    asc = next(p for p in day if p['direction'] == 'ascending')
    assert asc['platform'] == 'S1A' and asc['frames'] == 2
    desc = next(p for p in day if p['direction'] == 'descending')
    assert desc['platform'] == 'S1C' and desc['time'] == '21:54:02'


# ── match_sar_ais 端的整合 ───────────────────────────────────────────────────

def test_pass_candidates_uses_real_table_when_available():
    table = {'2026-07-08': [('ascending·S1A', m.parse_ts('2026-07-08T09:51:23Z'))]}
    rec = {'date': '2026-07-08'}
    cands = m.pass_candidates(rec, table)
    assert len(cands) == 1
    assert cands[0][0] == 'ascending·S1A'
    # 表裡沒有的日期 → 固定過境窗 fallback
    rec2 = {'date': '2026-07-09'}
    labels = [c[0] for c in m.pass_candidates(rec2, table)]
    assert labels == ['ascending', 'descending']
    # 自帶 timestamp 永遠優先
    rec3 = {'date': '2026-07-08', 'timestamp': '2026-07-08T21:53:12Z'}
    assert m.pass_candidates(rec3, table)[0][0] == 'exact'


def test_load_s1_pass_table_roundtrip(tmp_path):
    import json
    f = tmp_path / 's1.json'
    f.write_text(json.dumps({
        'passes': {
            '2026-07-08': [
                {'time': '09:51:23', 'direction': 'ascending', 'platform': 'S1A'},
                {'time': 'bogus', 'direction': 'ascending'},   # 壞列忽略
            ],
            '2026-07-09': [],                                  # 空日期忽略
        }
    }), encoding='utf-8')
    table = m.load_s1_pass_table(f)
    assert list(table) == ['2026-07-08']
    label, t = table['2026-07-08'][0]
    assert label == 'ascending·S1A'
    assert t == m.parse_ts('2026-07-08T09:51:23Z')
    # 缺檔 → 空 dict
    assert m.load_s1_pass_table(tmp_path / 'missing.json') == {}


def test_run_matching_with_real_pass_time():
    """真實過境時刻 09:51:23 時，固定窗 09:50 會差 83 秒 — 驗證比對
    直接用表中的時刻（match 的 pass label 帶平台）。"""
    t_pass = m.parse_ts('2026-07-10T09:51:23Z')
    table = {'2026-07-10': [('ascending·S1A', t_pass)]}
    tracks = {
        '412000009': {'name': 'REAL PASS', 'type_name': 'cargo', 'points': [
            (t_pass - 3600, 24.0, 120.0, 0.0, 0),
            (t_pass + 3600, 24.0, 120.0, 0.0, 0),
        ]},
    }
    dark = [{'lat': 24.0, 'lon': 120.0, 'date': '2026-07-10', 'detections': 1}]
    result = m.run_matching(dark, tracks, pass_table=table)
    assert result['summary']['rematched_local'] == 1
    assert result['summary']['s1_real_pass_dates'] == 1
    assert result['rematched'][0]['pass'] == 'ascending·S1A'
