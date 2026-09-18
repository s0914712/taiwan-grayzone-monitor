"""個案航跡圖的純函式測試 — plot_encounter.py

繪圖層在 render_case() 內才 import matplotlib，所以這裡不需要它。
"""
import plot_encounter as pe


def _p(t, lat, lon, speed):
    return {"t": t, "lat": lat, "lon": lon, "speed": speed}


def _case(track=None, **kw):
    case = {
        "window": {"from": "2026-08-13", "to": "2026-08-23"},
        "target": {"mmsi": "620999315", "name": "MEDNA", "type_name": "tanker",
                   "track": track if track is not None else
                   [_p("2026-08-13T03:00:00+00:00", 25.16, 122.87, 9.0)]},
        "companions": [],
        "dark_gaps": [],
        "markers": [],
    }
    case.update(kw)
    return case


# ── split_by_speed ────────────────────────────────────────────────────────
def test_split_by_speed_shares_boundary_points():
    """相鄰段共用邊界點，兩段線才接得起來（否則航跡會斷）。"""
    track = [_p("t1", 21.0, 121.0, 9.0), _p("t2", 21.1, 121.1, 8.0),
             _p("t3", 21.2, 121.2, 1.0), _p("t4", 21.3, 121.3, 0.5)]
    segs = pe.split_by_speed(track)
    assert [k for k, _ in segs] == ["transit", "drift"]
    assert segs[0][1][-1] is segs[1][1][0]      # 邊界點同一個物件
    assert len(segs[0][1]) == 3 and len(segs[1][1]) == 2


def test_split_by_speed_missing_speed_counts_as_under_way():
    """速度缺值不得被當成漂流 —— 那會憑空生出一段『停下來』。"""
    track = [_p("t1", 21.0, 121.0, None), _p("t2", 21.1, 121.1, None)]
    assert [k for k, _ in pe.split_by_speed(track)] == ["transit"]


def test_split_by_speed_empty_track():
    assert pe.split_by_speed([]) == []


# ── case_bounds ───────────────────────────────────────────────────────────
def _companion(lat, lon):
    return {"mmsi": "1", "name": "X", "type_name": "cargo",
            "track": [_p("t1", lat, lon, 10.0)]}


def test_case_bounds_default_frame_ignores_companions():
    """伴隨船是整段航程，納入外框會把目標船那段壓扁 —— 預設不納入。"""
    case = _case(track=[_p("t1", 21.0, 121.0, 1.0), _p("t2", 21.5, 121.5, 1.0)],
                 companions=[_companion(28.0, 128.0)])
    lat_min, lat_max, lon_min, lon_max = pe.case_bounds(case, pad=0.1)
    assert (round(lat_min, 2), round(lat_max, 2)) == (20.9, 21.6)
    assert (round(lon_min, 2), round(lon_max, 2)) == (120.9, 121.6)


def test_case_bounds_frame_all_includes_companions():
    case = _case(track=[_p("t1", 21.0, 121.0, 1.0)],
                 companions=[_companion(28.0, 128.0)])
    lat_min, lat_max, _, lon_max = pe.case_bounds(case, pad=0.0, frame="all")
    assert (lat_min, lat_max, lon_max) == (21.0, 28.0, 128.0)


def test_case_bounds_includes_drawn_markers():
    case = _case(track=[_p("t1", 21.0, 121.0, 1.0)])
    _, lat_max, _, _ = pe.case_bounds(case, pad=0.0,
                                      markers=[{"lat": 22.0, "lon": 121.0}])
    assert lat_max == 22.0


# ── select_markers ────────────────────────────────────────────────────────
def _marker(lat, lon, in_gap, km):
    return {"lat": lat, "lon": lon, "date": "2026-08-21",
            "in_dark_gap": in_gap, "km_from_dark": km}


def test_select_markers_keeps_only_nearby_blackout_detections():
    """同一天全台周邊都有暗船偵測；只有關機期間、又在關機海域附近的才相關。"""
    case = _case(markers=[_marker(21.9, 121.7, True, 18.5),     # 留
                          _marker(24.8, 120.2, True, 300.0),    # 太遠
                          _marker(21.9, 121.7, False, 5.0)])    # 不在關機期間
    kept = pe.select_markers(case)
    assert len(kept) == 1 and kept[0]["km_from_dark"] == 18.5


def test_select_markers_all_flag_keeps_everything():
    case = _case(markers=[_marker(21.9, 121.7, True, 18.5),
                          _marker(24.8, 120.2, True, 300.0)])
    assert len(pe.select_markers(case, all_markers=True)) == 2


# ── drift_bounds ──────────────────────────────────────────────────────────
def test_drift_bounds_covers_drift_and_blackout_only():
    """放大子圖只框漂流段與關機端點，不含高速航行的進出場段。"""
    case = _case(track=[_p("t1", 25.0, 122.8, 10.0),     # 高速進場，不納入
                        _p("t2", 21.5, 121.6, 1.0),
                        _p("t3", 21.6, 121.7, 0.5)],
                 dark_gaps=[{"last_lat": 21.8, "last_lon": 121.8,
                             "resume_lat": 21.3, "resume_lon": 121.7,
                             "gap_hours": 45.3, "drift_kn": 0.7}])
    lat_min, lat_max, lon_min, lon_max = pe.drift_bounds(case, pad=0.0)
    assert (lat_min, lat_max) == (21.3, 21.8)
    assert (lon_min, lon_max) == (121.6, 121.8)


def test_drift_bounds_none_without_drift_or_blackout():
    """全程都在航行 → 沒有值得放大的區域，不畫子圖。"""
    assert pe.drift_bounds(_case(track=[_p("t1", 25.0, 122.8, 10.0)])) is None


# ── info_lines ────────────────────────────────────────────────────────────
def test_info_lines_report_each_blackout_with_drift_speed():
    """關機多久、漂了多遠、平均幾節 —— 判讀的三個數字都要在框裡。"""
    case = _case(dark_gaps=[{"start": "2026-08-20T01:56:34+00:00",
                             "end": "2026-08-21T23:14:38+00:00",
                             "gap_hours": 45.3, "distance_km": 59.1,
                             "drift_kn": 0.7,
                             "last_lat": 21.8, "last_lon": 121.8,
                             "resume_lat": 21.3, "resume_lon": 121.7}])
    lines = pe.info_lines(case, markers=[])
    assert "MEDNA" in lines[0] and "620999315" in lines[0]
    assert any("45.3h" in ln and "0.7kn" in ln and "59.1km" in ln for ln in lines)
    assert not any("SAR" in ln for ln in lines)     # 沒有 marker 就不提


def test_info_lines_mention_sar_only_when_drawn():
    lines = pe.info_lines(_case(), markers=[_marker(21.9, 121.7, True, 18.5)])
    assert any("SAR dark detections during blackout: 1" in ln for ln in lines)
