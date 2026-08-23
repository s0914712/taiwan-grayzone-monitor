"""近 48h 公務／科研船名冊 — generate_dashboard.build_gov_recent()

首頁的公務船磚原本只讀當前 AIS 快照。實測 2026-08-21 23:14 UTC 之後
向陽紅03 與兩艘護航海警同時停止廣播，隔天早上首頁完全看不出來曾有這件事。
改以 tier-1 航跡歷史回溯 48 小時，停播的船仍列出並附「最後訊號距今幾小時」。
"""
import json
from datetime import datetime, timedelta, timezone

import generate_dashboard as gd

NOW = datetime(2026, 8, 22, 6, 0, tzinfo=timezone.utc)


def _snap(dt, vessels):
    return {"timestamp": dt.isoformat(), "vessels": vessels}


def _v(mmsi, name, cat, lat=23.6, lon=122.4, speed=5.0):
    return {"mmsi": mmsi, "name": name, "gov": cat, "type_name": cat,
            "lat": lat, "lon": lon, "speed": speed}


def _write(tmp_path, snaps):
    p = tmp_path / "ais_track_history.json"
    p.write_text(json.dumps(snaps), encoding="utf-8")
    return p


def test_vessel_silent_since_last_night_still_listed(tmp_path):
    """停播 7 小時的科研船仍在名冊上，並帶最後訊號時距。"""
    path = _write(tmp_path, [
        _snap(NOW - timedelta(hours=7),
              [_v("413701510", "XIANG YANG HONG 03", "research")]),
    ])
    out = gd.build_gov_recent(path, now=NOW)
    assert out["total"] == 1
    v = out["vessels"][0]
    assert v["name"] == "XIANG YANG HONG 03"
    assert v["gov_type"] == "research"
    assert v["age_hours"] == 7.0


def test_outside_window_dropped(tmp_path):
    path = _write(tmp_path, [
        _snap(NOW - timedelta(hours=60),
              [_v("413701510", "XIANG YANG HONG 03", "research")]),
    ])
    assert gd.build_gov_recent(path, now=NOW)["total"] == 0


def test_latest_position_wins(tmp_path):
    """同一艘船多次出現 → 取最後一次的位置與時間。"""
    path = _write(tmp_path, [
        _snap(NOW - timedelta(hours=20),
              [_v("413875040", "CHINACOASTGUARD2502", "coastguard",
                  lat=23.5, lon=122.1)]),
        _snap(NOW - timedelta(hours=3),
              [_v("413875040", "CHINACOASTGUARD2502", "coastguard",
                  lat=23.63, lon=122.60)]),
    ])
    v = gd.build_gov_recent(path, now=NOW)["vessels"][0]
    assert v["age_hours"] == 3.0
    assert v["lon"] == 122.60


def test_non_gov_vessels_excluded(tmp_path):
    path = _write(tmp_path, [
        _snap(NOW - timedelta(hours=1), [
            _v("413701510", "XIANG YANG HONG 03", "research"),
            {"mmsi": "412001", "name": "MINDONGYU1", "type_name": "fishing",
             "lat": 23.4, "lon": 117.6, "speed": 3.0},
        ]),
    ])
    out = gd.build_gov_recent(path, now=NOW)
    assert [v["mmsi"] for v in out["vessels"]] == ["413701510"]


def test_ordered_by_category_then_freshness(tmp_path):
    path = _write(tmp_path, [
        _snap(NOW - timedelta(hours=10),
              [_v("413701510", "XIANG YANG HONG 03", "research")]),
        _snap(NOW - timedelta(hours=8),
              [_v("413875040", "CHINACOASTGUARD2502", "coastguard")]),
        _snap(NOW - timedelta(hours=2),
              [_v("413875017", "CHINACOASTGUARD1306", "coastguard")]),
    ])
    out = gd.build_gov_recent(path, now=NOW)
    assert [v["mmsi"] for v in out["vessels"]] == [
        "413875017", "413875040", "413701510"]
    assert out["counts"] == {"coastguard": 2, "research": 1}


def test_missing_track_file_returns_none(tmp_path):
    assert gd.build_gov_recent(tmp_path / "nope.json", now=NOW) is None
