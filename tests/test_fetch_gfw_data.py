"""Tests for fetch_gfw_data.build_dark_detection_records (full-precision
unmatched-detection persistence for SAR × AIS re-matching)."""

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
