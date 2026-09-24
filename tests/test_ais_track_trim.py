"""fetch_ais_data.trim_track_history — 筆數上限 + 時間上限"""
from datetime import datetime, timedelta, timezone

from fetch_ais_data import trim_track_history

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def _entries(days_ago):
    return [{'timestamp': (NOW - timedelta(days=d)).isoformat()} for d in days_ago]


def test_age_cap_drops_entries_left_over_from_an_outage():
    # AIS 斷線 10 天：筆數沒到上限，但 39 天前的 entry 必須被丟掉
    hist = _entries([39, 35, 30, 5, 1, 0])
    out = trim_track_history(hist, 336, 28, now=NOW)
    assert [e['timestamp'] for e in out] == [e['timestamp'] for e in hist[3:]]


def test_count_cap_still_applies():
    out = trim_track_history(_entries([3, 2, 1, 0]), 2, 28, now=NOW)
    assert len(out) == 2


def test_unparseable_timestamp_is_kept():
    out = trim_track_history([{'timestamp': 'garbage'}], 10, 1, now=NOW)
    assert out == [{'timestamp': 'garbage'}]
