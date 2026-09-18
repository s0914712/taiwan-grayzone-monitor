"""軌跡保留期的回歸測試 — fetch_ais_data.trim_track_history()

保留期是**筆數**（最近 N 次觀測），不是天數。這看起來像 bug，其實是刻意的，
而且被試著「修」成按天數修剪過一次 —— 那是錯的，這個檔把理由釘住：

AIS 抓取會中斷（2026-09-07~17 的 Byteful 代理封鎖停擺 244.8 小時）。按天數
修剪會在剛恢復時把中斷前僅存的歷史一起丟掉：實測拿 2026-09-18 的 tier-1 套
14 天，168 筆 → 36 筆、12,028 艘船 → 8,565 艘，而海纜徘徊（需連續 ≥3h）、
Z 字型（需 ≥3 次轉向）、割草式測線全靠軌跡密度。按筆數留則安然度過。

代價是保留**期間**會伸縮，所以下游不可以拿「這個檔裡有這艘船」當成「近期
出現過」—— 那正是 analyze_suspicious.is_recently_active() 當初的錯，讓受制裁
油輪 MEDNA（620999315，最後一筆 AIS 2026-08-22）到 09-18 仍留在地圖上。
"""
from datetime import datetime, timedelta, timezone

import analyze_suspicious as asus
import fetch_ais_data as fad

NOW = datetime(2026, 9, 18, 21, 30, tzinfo=timezone.utc)


def _entry(when):
    return {'timestamp': when.isoformat(), 'vessel_count': 0, 'vessels': []}


def _span_days(history):
    first = datetime.fromisoformat(history[0]['timestamp'])
    last = datetime.fromisoformat(history[-1]['timestamp'])
    return (last - first).total_seconds() / 86400


def test_keeps_the_most_recent_observations():
    hist = [_entry(NOW - timedelta(hours=2 * i)) for i in range(200, -1, -1)]
    out = fad.trim_track_history(hist, fad.AIS_TRACK_MAX_ENTRIES)
    assert len(out) == fad.AIS_TRACK_MAX_ENTRIES
    assert out[-1] == hist[-1]          # 留最新，不是最舊
    assert out[0] == hist[-fad.AIS_TRACK_MAX_ENTRIES]


def test_outage_does_not_wipe_the_history():
    """抓取中斷 10 天後恢復，中斷前的軌跡必須還在。

    這是按筆數而非按天數保留的唯一理由。若改成 14 天修剪，這裡只會剩下
    恢復後那幾筆，行為偵測（連續徘徊、轉向次數）在最需要回溯時失效。
    """
    before = [_entry(NOW - timedelta(days=12) - timedelta(hours=2 * i))
              for i in range(60, 0, -1)]          # 中斷前 5 天、每 2h 一筆
    after = [_entry(NOW - timedelta(hours=2 * i)) for i in range(12, -1, -1)]
    hist = before + after                          # 中間空了 ~10 天
    out = fad.trim_track_history(hist, fad.AIS_TRACK_MAX_ENTRIES)

    assert len(out) == len(hist), '筆數未達上限時不該丟任何資料'
    assert out[0] == before[0], '中斷前的最舊一筆仍在'
    assert _span_days(out) > 14, '保留期間確實會超過 14 天 —— 這是預期行為'


def test_entry_cap_still_bounds_the_file():
    """即使全部落在 14 天內，筆數上限仍要守住檔案大小。"""
    dense = [_entry(NOW - timedelta(minutes=30 * i)) for i in range(400, -1, -1)]
    assert _span_days(dense) < 14
    assert len(fad.trim_track_history(dense, fad.AIS_TRACK_MAX_ENTRIES)) == \
        fad.AIS_TRACK_MAX_ENTRIES


def test_tier2_keeps_more_entries_than_tier1():
    assert fad.AIS_TRACK_COMMERCIAL_MAX_ENTRIES > fad.AIS_TRACK_MAX_ENTRIES


def test_downstream_must_not_treat_presence_as_recency():
    """保留期會伸縮，所以「檔案裡有這艘船」≠「近期出現過」。

    與上面的保留策略成對：留著舊資料是對的，把舊資料當成新的才是 bug。
    """
    stale_track = [{'t': (NOW - timedelta(days=27)).isoformat(),
                    'lat': 20.91, 'lon': 120.96, 'speed': 7.0}]
    assert asus.is_recently_active({}, stale_track, now=NOW) is False
    fresh_track = [{'t': (NOW - timedelta(days=1)).isoformat(),
                    'lat': 20.91, 'lon': 120.96, 'speed': 7.0}]
    assert asus.is_recently_active({}, fresh_track, now=NOW) is True
