"""Tests for fetch_gfw_data.build_dark_detection_records (full-precision
unmatched-detection persistence for SAR × AIS re-matching)."""

import json

import fetch_gfw_data as g


def test_matched_records_are_skipped():
    records = [
        {'vesselId': 'abc', 'lat': 24.0, 'lon': 120.0, 'date': '2026-07-01'},
        {'lat': 24.12345, 'lon': 120.54321, 'date': '2026-07-01', 'detections': 2},
    ]
    out = g.build_dark_detection_records(records)
    assert len(out) == 1
    assert out[0]['lat'] == 24.12345          # full precision preserved
    assert out[0]['lon'] == 120.54321
    assert out[0]['detections'] == 2


def test_invalid_coordinates_are_skipped():
    records = [
        {'lat': None, 'lon': 120.0, 'date': '2026-07-01'},
        {'lat': 'x', 'lon': 120.0, 'date': '2026-07-01'},
        {'latitude': 24.5, 'longitude': 121.5, 'date': '2026-07-01'},
    ]
    out = g.build_dark_detection_records(records)
    assert len(out) == 1
    assert out[0]['lat'] == 24.5 and out[0]['lon'] == 121.5


def test_optional_fields_pass_through():
    records = [
        {'lat': 24.0, 'lon': 120.0, 'date': '2026-07-01T00:00:00Z',
         'timestamp': '2026-07-01T09:52:11Z', 'length': 88.5},
        {'lat': 24.1, 'lon': 120.1, 'date': '2026-07-01'},
    ]
    out = g.build_dark_detection_records(records)
    assert out[0]['date'] == '2026-07-01'      # trimmed to YYYY-MM-DD
    assert out[0]['timestamp'] == '2026-07-01T09:52:11Z'
    assert out[0]['length_m'] == 88.5
    assert 'timestamp' not in out[1] and 'length_m' not in out[1]


# ── 空回應保護（2026-07-29 迴歸） ─────────────────────────────────────────
# GFW 回 HTTP 200 但 0 筆，把 2,305 筆的 dark_vessels.json 整份洗成 0，
# 暗船頁全空、統計頁卻還有累積歷史。有既有資料時不得覆寫。

def test_previous_detection_total_reads_existing_file(tmp_path):
    p = tmp_path / 'dark_vessels.json'
    p.write_text(json.dumps({'overall': {'total_detections': 2305}}), encoding='utf-8')
    assert g.previous_detection_total(p) == 2305


def test_previous_detection_total_missing_or_broken_file_is_zero(tmp_path):
    assert g.previous_detection_total(tmp_path / 'nope.json') == 0
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json', encoding='utf-8')
    assert g.previous_detection_total(bad) == 0
    empty = tmp_path / 'empty.json'
    empty.write_text(json.dumps({'overall': {'total_detections': 0}}), encoding='utf-8')
    assert g.previous_detection_total(empty) == 0


# ── 逐日總偵測數（每日暗船「比例」的分母） ────────────────────────────
# Sentinel-1 不是每天過境、每次掃的幅寬也不同，每日暗船「數量」主要反映
# 衛星覆蓋；沒有同日分母就畫不出有意義的趨勢。

def test_total_by_date_counts_every_record_not_just_dark():
    records = [
        {'vesselId': 'a', 'lat': 24.0, 'lon': 120.0, 'date': '2026-09-08'},
        {'lat': 24.1, 'lon': 120.1, 'date': '2026-09-08'},
        {'lat': 24.2, 'lon': 120.2, 'date': '2026-09-08'},
        {'vesselId': 'b', 'lat': 24.3, 'lon': 120.3, 'date': '2026-09-09'},
    ]
    out = g.build_region_summary(records)
    assert out['total_by_date'] == {'2026-09-08': 3, '2026-09-09': 1}
    assert out['dark_by_date'] == {'2026-09-08': 2}
    # 分母涵蓋「有偵測但零暗船」的日子，分子不會
    assert set(out['total_by_date']) - set(out['dark_by_date']) == {'2026-09-09'}


def test_total_by_date_ignores_records_without_date():
    records = [{'lat': 24.0, 'lon': 120.0, 'date': ''},
               {'lat': 24.1, 'lon': 120.1, 'date': '2026-09-08'}]
    out = g.build_region_summary(records)
    assert out['total_by_date'] == {'2026-09-08': 1}
    assert out['total_detections'] == 2      # 總數仍然算進去


# ── 偵測時刻：entryTimestamp（欄位名先前抓錯，永遠拿不到時刻） ────────

def test_entry_timestamp_is_used_when_unambiguous():
    rec = {'entryTimestamp': '2026-09-09T09:52:29Z',
           'exitTimestamp': '2026-09-09T09:52:29Z'}
    assert g.extract_detection_timestamp(rec, '2026-09-09') == '2026-09-09T09:52:29Z'


def test_timestamp_rejected_when_row_covers_multiple_passes():
    # entry != exit → 這列聚合了不只一次成像，時刻不明確
    rec = {'entryTimestamp': '2026-09-09T09:52:29Z',
           'exitTimestamp': '2026-09-09T21:55:03Z'}
    assert g.extract_detection_timestamp(rec, '2026-09-09') is None


def test_timestamp_rejected_when_flattened_to_query_window():
    # 坑 #8：長 date-range 會把 entry/exitTimestamp 壓成整個查詢區間
    rec = {'entryTimestamp': '2026-08-13T00:00:00Z',
           'exitTimestamp': '2026-08-13T00:00:00Z'}
    assert g.extract_detection_timestamp(rec, '2026-09-09') is None


def test_timestamp_requires_a_date_to_check_against():
    rec = {'entryTimestamp': '2026-09-09T09:52:29Z'}
    assert g.extract_detection_timestamp(rec, '') is None


def test_build_dark_detection_records_reads_entry_timestamp():
    records = [
        {'lat': 24.0, 'lon': 120.0, 'date': '2026-09-08',
         'entryTimestamp': '2026-09-08T09:52:11Z',
         'exitTimestamp': '2026-09-08T09:52:11Z'},
        {'lat': 24.1, 'lon': 120.1, 'date': '2026-09-09',
         'entryTimestamp': '2026-09-09T21:55:03Z',
         'exitTimestamp': '2026-09-09T21:55:03Z'},
    ]
    out = g.build_dark_detection_records(records)
    assert [r['timestamp'] for r in out] == ['2026-09-08T09:52:11Z',
                                             '2026-09-09T21:55:03Z']


def test_flattened_timestamps_are_dropped_wholesale():
    # 區間起始日那天會騙過逐列的日期檢查 → 整批防呆必須擋下來
    records = [
        {'lat': 24.0, 'lon': 120.0, 'date': '2026-08-13',
         'entryTimestamp': '2026-08-13T00:00:00Z',
         'exitTimestamp': '2026-08-13T00:00:00Z'},
        {'lat': 24.1, 'lon': 120.1, 'date': '2026-08-20'},
        {'lat': 24.2, 'lon': 120.2, 'date': '2026-09-09'},
    ]
    out = g.build_dark_detection_records(records)
    assert all('timestamp' not in r for r in out)


def test_drop_flattened_keeps_genuinely_single_date_batches():
    # 單日批次（所有偵測都在同一天）不該被誤判成壓平
    dark = [{'date': '2026-09-09', 'timestamp': '2026-09-09T09:52:29Z'},
            {'date': '2026-09-09', 'timestamp': '2026-09-09T09:52:29Z'}]
    assert g.drop_flattened_timestamps(dark) == 2
    assert all('timestamp' in r for r in dark)
