"""歷史個案重建的純函式測試 — archive_case.py

掃 archive 的部分要 git，測不了；這裡守住的是判讀邏輯：哪些檔該掃、
誰算接近、哪些 SAR 點落在關機期間。
"""
import archive_case as ac

PATHS = [
    "archive/ais_track_2026-07.jsonl",
    "archive/ais_commercial_2026-07.jsonl",
    "archive/ais_track_2026-08.jsonl",
    "archive/ais_commercial_2026-08.jsonl",
    "archive/2026-08/19/ais_track_20260819T031500Z.jsonl.gz",
    "archive/2026-08/28/ais_track_20260828T031500Z.jsonl.gz",
    "archive/2026-09/01/ais_commercial_20260901T013730Z.jsonl.gz",
]


def test_relevant_files_picks_month_and_day_layouts():
    """月檔靠檔名、每次執行一檔靠路徑日期 —— 兩種命名並存。"""
    picked = ac.relevant_files(PATHS, "2026-08-13", "2026-08-23")
    assert "archive/ais_track_2026-08.jsonl" in picked
    assert "archive/ais_commercial_2026-08.jsonl" in picked
    assert "archive/2026-08/19/ais_track_20260819T031500Z.jsonl.gz" in picked
    # 視窗外的月檔與日檔都不掃
    assert not any("2026-07" in p for p in picked)
    assert not any("/28/" in p or "2026-09" in p for p in picked)


def test_relevant_files_spans_intermediate_months():
    """跨月的個案要把中間月份的月檔也帶上。"""
    picked = ac.relevant_files(PATHS, "2026-07-20", "2026-09-02")
    assert "archive/ais_track_2026-07.jsonl" in picked
    assert "archive/ais_track_2026-08.jsonl" in picked
    assert "archive/2026-09/01/ais_commercial_20260901T013730Z.jsonl.gz" in picked


# ── closest_approach ──────────────────────────────────────────────────────
def _pt(t, lat, lon):
    return {"t": t, "lat": lat, "lon": lon, "speed": 1.0}


def test_closest_approach_matches_on_nearest_timestamp():
    target = [_pt("2026-08-14T03:00:00+00:00", 21.30, 121.70),
              _pt("2026-08-14T05:00:00+00:00", 21.20, 121.50)]
    other = [_pt("2026-08-14T05:00:00+00:00", 21.21, 121.50)]
    km, at = ac.closest_approach(target, other)
    assert km < 2.0
    assert at == "2026-08-14T05:00:00+00:00"


def test_closest_approach_skips_points_with_no_matching_fix():
    """對方在該時段根本沒回報時不得硬配 —— 那會算出假的接近距離。"""
    target = [_pt("2026-08-14T03:00:00+00:00", 21.30, 121.70)]
    other = [_pt("2026-08-16T03:00:00+00:00", 21.30, 121.70)]
    assert ac.closest_approach(target, other) == (None, None)


def test_closest_approach_empty_tracks():
    assert ac.closest_approach([], [_pt("t", 21.0, 121.0)]) == (None, None)


# ── pick_companions ───────────────────────────────────────────────────────
def _companions(*mmsis):
    return {m: {"mmsi": m, "name": f"SHIP{m}", "type_name": "tanker",
                "track": [_pt("2026-08-14T03:00:00+00:00", 21.30, 121.70)]}
            for m in mmsis}


def test_pick_companions_forced_entries_do_not_consume_the_limit():
    """指名納入的船（制裁命中、姊妹船）不佔距離排序的名額。"""
    target = [_pt("2026-08-14T03:00:00+00:00", 21.30, 121.70)]
    comps = _companions("A", "B", "C", "FORCED")
    encounters = [{"mmsi": "A", "km": 2.0, "t": "2026-08-14T03:00:00+00:00"},
                  {"mmsi": "B", "km": 3.0, "t": "2026-08-14T03:00:00+00:00"},
                  {"mmsi": "C", "km": 4.0, "t": "2026-08-14T03:00:00+00:00"}]
    kept = ac.pick_companions(target, encounters, comps, limit=2,
                              include=["FORCED"])
    assert {c["mmsi"] for c in kept} == {"FORCED", "A", "B"}
    assert next(c for c in kept if c["mmsi"] == "FORCED")["forced"] is True
    assert next(c for c in kept if c["mmsi"] == "A")["forced"] is False


def test_pick_companions_ignores_unknown_forced_mmsi():
    """指名一艘資料裡沒有的船不該炸掉整份個案。"""
    target = [_pt("2026-08-14T03:00:00+00:00", 21.30, 121.70)]
    kept = ac.pick_companions(target, [], _companions("A"), limit=1,
                              include=["NOT_PRESENT"])
    assert [c["mmsi"] for c in kept] == []


# ── tag_markers_in_dark ───────────────────────────────────────────────────
GAP = {"start": "2026-08-20T01:56:34+00:00", "end": "2026-08-21T23:14:38+00:00",
       "last_lat": 21.807, "last_lon": 121.801,
       "resume_lat": 21.285, "resume_lon": 121.698}


def test_tag_markers_flags_blackout_dates_and_distance():
    markers = [{"lat": 21.93, "lon": 121.68, "date": "2026-08-21"},
               {"lat": 22.28, "lon": 120.52, "date": "2026-08-21"},
               {"lat": 21.93, "lon": 121.68, "date": "2026-08-25"}]
    tagged = ac.tag_markers_in_dark(markers, [GAP])
    assert tagged[0]["in_dark_gap"] is True
    assert 15 < tagged[0]["km_from_dark"] < 25      # 實測 18.5km
    assert tagged[1]["in_dark_gap"] is True         # 同日但在台灣西岸
    assert tagged[1]["km_from_dark"] > 100          # 距離才分得開相關性
    assert tagged[2]["in_dark_gap"] is False        # 關機結束後才成像


def test_tag_markers_without_gaps_marks_nothing():
    tagged = ac.tag_markers_in_dark(
        [{"lat": 21.93, "lon": 121.68, "date": "2026-08-21"}], [])
    assert tagged[0]["in_dark_gap"] is False


# ── pass_reachability / classify_markers_vs_target ────────────────────────
# MEDNA 實測：2026-08-20T01:56 關機於 21.807/121.801，08-21T23:14 於
# 21.285/121.698 復播。該日三次真實 Sentinel-1 過境（data/s1_pass_times.json）。
MEDNA_GAP = {"start": "2026-08-20T01:56:34+00:00", "end": "2026-08-21T23:14:38+00:00",
             "last_lat": 21.807262, "last_lon": 121.801007,
             "resume_lat": 21.284765, "resume_lon": 121.698443}
MEDNA_PASSES = {
    "2026-08-20": [("ascending·S1C", ac._epoch("2026-08-20T09:22:07+00:00")),
                   ("ascending·S1D", ac._epoch("2026-08-20T10:08:56+00:00"))],
    "2026-08-21": [("ascending·S1C", ac._epoch("2026-08-21T09:57:56+00:00")),
                   ("ascending·S1D", ac._epoch("2026-08-21T10:48:31+00:00")),
                   ("descending·S1C", ac._epoch("2026-08-21T22:05:00+00:00"))],
}
NEARBY_DETECTION = {"lat": 21.93, "lon": 121.68, "date": "2026-08-21",
                    "in_dark_gap": True}


def test_pass_reachability_rules_out_the_late_pass():
    """22:05 降軌距離復播只剩 69 分鐘，而偵測點離復播位置 72 公里 ——
    要 33 節才追得回來，商船做不到，所以那個時刻的偵測不可能是這艘船。
    早上兩次升軌相隔 12-13 小時，3 節就夠，分不出來。"""
    checks = ac.pass_reachability(dict(NEARBY_DETECTION), MEDNA_GAP, MEDNA_PASSES)
    assert len(checks) == 3
    late = next(c for c in checks if c["pass"].startswith("descending"))
    assert late["return_kn"] > 30 and late["feasible"] is False
    assert all(c["feasible"] for c in checks if c["pass"].startswith("ascending"))


def test_pass_reachability_skips_passes_outside_the_blackout():
    """關機區間外的時刻船正在播報 AIS，位置是已知的 —— 不歸這裡判。"""
    outside = {"2026-08-21": [("ascending·S1C",
                               ac._epoch("2026-08-25T09:57:56+00:00"))]}
    assert ac.pass_reachability(dict(NEARBY_DETECTION), MEDNA_GAP, outside) == []


def test_classify_markers_verdicts():
    # 關機第 8 小時、380 公里外（台灣西岸）—— 要 27 節才趕得到，確定是別艘船。
    # 同一個位置若換成關機第 32 小時的過境就變成「到得了」，所以**距離本身不是
    # 判準，時間才是**；這正是要靠真實過境時刻而非固定過境窗的原因。
    far = {"lat": 24.86, "lon": 120.14, "date": "2026-08-20", "in_dark_gap": True}
    out_of_gap = {"lat": 21.93, "lon": 121.68, "date": "2026-08-25",
                  "in_dark_gap": False}
    markers = ac.classify_markers_vs_target(
        [dict(NEARBY_DETECTION), far, out_of_gap], [MEDNA_GAP], MEDNA_PASSES)
    assert markers[0]["verdict"] == "could_be_target"
    assert markers[1]["verdict"] == "not_target"
    assert "verdict" not in markers[2]                # 不在關機期間，不判


def test_same_position_flips_verdict_with_the_pass_time():
    """同一個偵測位置，換一天的過境就從『不可能』變成『可能』 ——
    判準是時間不是距離，固定過境窗會把這件事算錯。"""
    far_early = {"lat": 24.86, "lon": 120.14, "date": "2026-08-20",
                 "in_dark_gap": True}
    far_late = {"lat": 24.86, "lon": 120.14, "date": "2026-08-21",
                "in_dark_gap": True}
    out = ac.classify_markers_vs_target([far_early, far_late], [MEDNA_GAP],
                                        MEDNA_PASSES)
    assert out[0]["verdict"] == "not_target"
    assert out[1]["verdict"] == "could_be_target"


def test_classify_markers_unknown_without_pass_times():
    """該日沒抓到真實過境時刻就誠實標 unknown，不要猜。"""
    markers = ac.classify_markers_vs_target(
        [dict(NEARBY_DETECTION)], [MEDNA_GAP], {})
    assert markers[0]["verdict"] == "unknown"
