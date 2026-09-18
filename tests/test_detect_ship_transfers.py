"""STS 偵測 tier-2 合併修正的回歸測試 — detect_ship_transfers.py

驗證核心缺口已修：偵測器現在合併 tier-1（漁船/公務船）+ tier-2（商船/油輪），
才能抓到**油輪↔油輪的海上轉油（影子船隊）**——先前只讀 tier-1，tanker-to-tanker
STS 永遠偵測不到。
"""
import json
import math

import detect_ship_transfers as dst


def _snap(ts, vessels):
    return {"timestamp": ts, "period_key": ts[:13],  # 同小時共用 period_key
            "vessel_count": len(vessels), "vessels": vessels}


def _v(mmsi, name, lat, lon, typ, speed=0.2):
    return {"mmsi": mmsi, "name": name, "lat": lat, "lon": lon,
            "speed": speed, "heading": 10, "type_name": typ}


# 兩艘油輪並靠 ~9m（0.00008° 緯度）於基隆東北外海（非港內）
def _tanker_A():
    return _v("667001650", "HUIXIN", 25.85000, 121.6000, "tanker")


def _tanker_B():
    return _v("352004775", "WEALTHY", 25.85008, 121.6000, "tanker")


def _times(hours):
    return [f"2026-07-01T{h:02d}:30:00+00:00" for h in hours]


def _patch(monkeypatch, tmp_path, tier1, tier2, snapshot_missing=True):
    t1 = tmp_path / "tier1.json"
    t2 = tmp_path / "tier2.json"
    t1.write_text(json.dumps(tier1), encoding="utf-8")
    t2.write_text(json.dumps(tier2), encoding="utf-8")
    monkeypatch.setattr(dst, "TRACK_HISTORY_FILE", t1)
    monkeypatch.setattr(dst, "TRACK_COMMERCIAL_FILE", t2)
    if snapshot_missing:
        monkeypatch.setattr(dst, "SNAPSHOT_FILE", tmp_path / "no_snapshot.json")


def _pair_keys(events):
    return {ev["pair_key"] for ev in events}


def test_tanker_tanker_sts_detected_via_tier2(monkeypatch, tmp_path):
    """兩艘油輪並靠 3 個時段（4h）→ 合併 tier-2 後偵測到該 STS。"""
    ts = _times([0, 2, 4])
    tier2 = [_snap(t, [_tanker_A(), _tanker_B()]) for t in ts]
    tier1 = [_snap(t, []) for t in ts]  # tier-1 空（無漁船）
    _patch(monkeypatch, tmp_path, tier1, tier2)

    events = dst.process_track_history()
    assert ("352004775", "667001650") in _pair_keys(events)


def test_tanker_sts_invisible_with_tier1_only(monkeypatch, tmp_path):
    """對照組：同樣的油輪只放進 tier-1 掃描來源會不會抓到？
    這裡把 tier-2 清空、油輪只在 tier-1（模擬舊行為的資料流），確認
    偵測邏輯本身沒問題；真正的修正在於 tier-2 也被納入掃描（上一個測試）。"""
    ts = _times([0, 2, 4])
    tier1 = [_snap(t, [_tanker_A(), _tanker_B()]) for t in ts]
    tier2 = [_snap(t, []) for t in ts]
    _patch(monkeypatch, tmp_path, tier1, tier2)

    events = dst.process_track_history()
    assert ("352004775", "667001650") in _pair_keys(events)


def test_tier2_missing_is_noop(monkeypatch, tmp_path):
    """tier-2 檔案不存在時，合併是 no-op，tier-1 配對照常偵測、不報錯。"""
    ts = _times([0, 2, 4])
    tier1 = [_snap(t, [_tanker_A(), _tanker_B()]) for t in ts]
    t1 = tmp_path / "tier1.json"
    t1.write_text(json.dumps(tier1), encoding="utf-8")
    monkeypatch.setattr(dst, "TRACK_HISTORY_FILE", t1)
    monkeypatch.setattr(dst, "TRACK_COMMERCIAL_FILE", tmp_path / "absent.json")
    monkeypatch.setattr(dst, "SNAPSHOT_FILE", tmp_path / "no_snap.json")

    events = dst.process_track_history()  # 不應拋例外
    assert ("352004775", "667001650") in _pair_keys(events)


def test_merge_keys_on_timestamp_not_period_key(monkeypatch, tmp_path):
    """同一 period_key、不同 timestamp 的重跑快照不可被合併 ——
    否則移動中的船會被併在一起灌出假配對。兩艘船在 t=0 相距很遠、
    在同 period_key 的另一時刻各自移動；不同 timestamp 應保持分離。"""
    # 兩艘船從不相鄰 → 各自移動，任一單一 timestamp 內都不並靠
    far_A = _v("111", "A", 25.80, 121.50, "cargo")
    far_B = _v("222", "B", 25.90, 121.70, "cargo")  # ~24km away
    # 同 period_key（同小時）但不同 timestamp 的兩份快照
    tier1 = [
        _snap("2026-07-01T00:10:00+00:00", [far_A, far_B]),
        _snap("2026-07-01T00:50:00+00:00", [far_A, far_B]),
    ]
    tier2 = []
    _patch(monkeypatch, tmp_path, tier1, tier2)

    snaps = dst.load_merged_snapshots()
    # 用 timestamp 當鍵 → 2 份；若誤用 period_key → 會塌成 1 份
    assert len(snaps) == 2


# ──────────────────────────────────────────────────────────────────────────
# 漂流會合（10m–5km）與關機事件 —— 旁靠層的 10 公尺門檻對大船形同關閉，
# 這兩層補的是 VLCC 級過駁：停下來碰面、以及兩邊都關掉 AIS。
# 座標取巴士海峽外海（21.6N/121.6E），遠離任何港口排除區。
# ──────────────────────────────────────────────────────────────────────────
RV_LAT, RV_LON = 21.6000, 121.6000
# 該緯度下「1 公里」換算成經度差（經線在高緯收窄，須除以 cos(lat)）
KM_IN_LON_DEG = 1 / (111.0 * math.cos(math.radians(RV_LAT)))


def _rv_snaps(v1_factory, v2_factory, hours=(0, 3, 6, 9)):
    """同一對船在數個時刻的合併快照。"""
    return [_snap(f"2026-08-18T{h:02d}:00:00+00:00", [v1_factory(), v2_factory()])
            for h in hours]


def _drifting(mmsi, name, typ, km_east=0.0, speed=1.0):
    return _v(mmsi, name, RV_LAT, RV_LON + km_east * KM_IN_LON_DEG, typ, speed=speed)


def test_drift_rendezvous_detected_at_km_scale():
    """兩艘油輪相距 1.5km、雙方 1kn、持續 9 小時 → 會合事件；旁靠層看不到。"""
    snaps = _rv_snaps(lambda: _drifting("620999315", "MEDNA", "tanker"),
                      lambda: _drifting("613002360", "YVICTORY", "tanker", km_east=1.5))
    assert not dst.process_track_history(snaps)  # 旁靠層（10m）：無事件
    records = dst.build_rendezvous_records(snaps)
    assert len(records) == 1
    rec = records[0]
    assert rec["duration_hours"] == 9.0
    assert 1400 < rec["min_distance_m"] < 1600
    assert {rec["vessel1"]["mmsi"], rec["vessel2"]["mmsi"]} == {"620999315", "613002360"}


def test_drift_rendezvous_ignores_fishing_pair():
    """雙方皆為漁船不計 —— 5km 的窗口放進漁場會把整支船隊兩兩配對。"""
    snaps = _rv_snaps(lambda: _drifting("416000391", "WANN YIH TZAY", "fishing"),
                      lambda: _drifting("416000986", "YU FU", "fishing", km_east=1.0))
    assert dst.build_rendezvous_records(snaps) == []


def test_drift_rendezvous_requires_a_commercial_side():
    """至少一方需為 tanker/cargo/lng；兩艘科研船碰面不走這條規則。"""
    snaps = _rv_snaps(lambda: _drifting("413547290", "XIANG YANG HONG 05", "research"),
                      lambda: _drifting("412000001", "SHIYAN 6", "research", km_east=1.0))
    assert dst.build_rendezvous_records(snaps) == []


def test_alongside_band_left_to_the_sts_layer():
    """相距 9 公尺屬旁靠層，不重複出現在會合層。"""
    snaps = _rv_snaps(_tanker_A, _tanker_B)
    assert dst.find_rendezvous_in_snapshot(snaps[0]["vessels"]) == []
    assert dst.find_pairs_in_snapshot(snaps[0]["vessels"])


def test_rendezvous_needs_minimum_duration():
    """只碰到一個快照（0 小時）不算會合。"""
    snaps = _rv_snaps(lambda: _drifting("620999315", "MEDNA", "tanker"),
                      lambda: _drifting("613002360", "YVICTORY", "tanker", km_east=1.5),
                      hours=(0,))
    assert dst.build_rendezvous_records(snaps) == []


def _dark_snaps(mmsi, name, points):
    """points: [(ts, lat, lon)] → 每個時刻一份單船快照。"""
    return [_snap(ts, [_v(mmsi, name, lat, lon, "tanker", speed=1.0)])
            for ts, lat, lon in points]


# MEDNA 實測：2026-08-20T01:56 關機於 21.807/121.801，
# 08-21T23:14 於 21.285/121.698 復播 —— 45.3 小時只移動 59 公里 = 0.7 節。
MEDNA_DARK = [("2026-08-20T01:56:00+00:00", 21.8073, 121.8010),
              ("2026-08-21T23:14:00+00:00", 21.2848, 121.6984)]


def test_dark_gap_separates_drifting_from_transiting():
    """關機後在漂（0.7kn）與關機趕路（3kn）必須分得開。"""
    timelines = dst.build_vessel_timelines(_dark_snaps("620999315", "MEDNA", MEDNA_DARK))
    gaps = dst.find_dark_gaps(timelines)
    assert len(gaps) == 1
    assert gaps[0]["gap_hours"] == 45.3
    assert gaps[0]["drift_kn"] < dst.DARK_DRIFT_MAX_KN

    # 同樣長度的靜默，但跑了 300 公里 → 是關機趕路，不是過駁
    transit = [("2026-08-20T01:56:00+00:00", 21.8073, 121.8010),
               ("2026-08-21T23:14:00+00:00", 24.5000, 121.8010)]
    gaps2 = dst.find_dark_gaps(dst.build_vessel_timelines(
        _dark_snaps("111111111", "TRANSITER", transit)))
    assert gaps2[0]["drift_kn"] > dst.DARK_DRIFT_MAX_KN


def test_dark_gap_ignores_short_and_never_resumed():
    """未達門檻的空檔、以及駛出監測範圍（沒有下一點）都不是關機。"""
    short = [("2026-08-20T01:00:00+00:00", 21.80, 121.80),
             ("2026-08-20T05:00:00+00:00", 21.79, 121.79)]
    assert dst.find_dark_gaps(dst.build_vessel_timelines(
        _dark_snaps("222222222", "SHORT GAP", short))) == []
    one_point = [("2026-08-20T01:00:00+00:00", 21.80, 121.80)]
    assert dst.find_dark_gaps(dst.build_vessel_timelines(
        _dark_snaps("333333333", "LEFT AREA", one_point))) == []


def test_codark_requires_overlap_and_proximity():
    """兩船靜默區間重疊 ≥6h 且失訊處 ≤30km 才算共同關機。"""
    a = dst.find_dark_gaps(dst.build_vessel_timelines(
        _dark_snaps("620999315", "MEDNA", MEDNA_DARK)))
    # 同時段、同海域（約 11km 外）一起消失 → 成對
    near = [("2026-08-20T02:00:00+00:00", 21.9073, 121.8010),
            ("2026-08-21T22:00:00+00:00", 21.4000, 121.7000)]
    b = dst.find_dark_gaps(dst.build_vessel_timelines(
        _dark_snaps("613002360", "YVICTORY", near)))
    pairs = dst.find_codark_pairs(sorted(a + b, key=lambda g: g["start"]))
    assert len(pairs) == 1
    assert pairs[0]["separation_km"] < dst.CODARK_MAX_SEPARATION_KM
    assert pairs[0]["both_drifting"] is True

    # 同時段但在 400km 外消失 → 不成對
    far = [("2026-08-20T02:00:00+00:00", 25.4000, 121.8010),
           ("2026-08-21T22:00:00+00:00", 25.3000, 121.7000)]
    c = dst.find_dark_gaps(dst.build_vessel_timelines(
        _dark_snaps("444444444", "FAR AWAY", far)))
    assert dst.find_codark_pairs(sorted(a + c, key=lambda g: g["start"])) == []


def test_zero_speed_blackout_counts_as_drifting():
    """靜默期間完全沒移動（0.0 kn）最像過駁 —— 不可因為 0 是 falsy 被判成航行。"""
    still = [("2026-08-20T01:00:00+00:00", 21.8073, 121.8010),
             ("2026-08-21T23:00:00+00:00", 21.8073, 121.8010)]
    gaps = dst.find_dark_gaps(dst.build_vessel_timelines(
        _dark_snaps("555555555", "STILL", still)))
    assert gaps[0]["drift_kn"] == 0.0
    assert dst.is_drifting_gap(gaps[0]) is True


def test_dark_gaps_skip_fishing_fleet():
    """漁船停播再漂一天是日常；放行全船隊會把整支船隊兩兩配對成『共同關機』。"""
    snaps = [_snap(ts, [_v("412345678", "MINDONGYU63179", lat, lon, "fishing", 0.3)])
             for ts, lat, lon in MEDNA_DARK]
    assert dst.find_dark_gaps(dst.build_vessel_timelines(snaps)) == []
    # 關掉船型過濾（分析用）時仍抓得到
    assert len(dst.find_dark_gaps(dst.build_vessel_timelines(snaps), types=None)) == 1


def test_dark_gap_ignores_spans_longer_than_the_window():
    """頭尾各出現一次不是關機 —— 兩端直線距離除以 600 小時得到的「0.3 節」，
    只代表這艘船那段期間跑去別的地方了。"""
    long_span = [("2026-08-22T13:00:00+00:00", 21.8073, 121.8010),
                 ("2026-09-17T21:00:00+00:00", 24.5000, 118.0000)]
    gaps = dst.find_dark_gaps(dst.build_vessel_timelines(
        _dark_snaps("666666666", "LONG SPAN", long_span)))
    assert gaps == []


# 台中外錨地（24.27/120.52，離岸 2.6km）—— 等泊位的船排排站，不是海上過駁
ANCHORAGE_LAT, ANCHORAGE_LON = 24.2700, 120.5200


def test_rendezvous_ignores_coastal_anchorage():
    """離岸門檻是這一層唯一擋得住錨地的東西。

    港口清單補不完：台中外錨地、麥寮、閩江口都不在 CN_PORTS/PORTS 的半徑內，
    未設離岸門檻時光是這些地方就灌出上萬組配對。
    """
    def _at_anchorage(mmsi, name):
        return _v(mmsi, name, ANCHORAGE_LAT, ANCHORAGE_LON + 0.02, "tanker", 0.0)

    snaps = [_snap(f"2026-08-18T{h:02d}:00:00+00:00",
                   [_v("111000001", "WAITING A", ANCHORAGE_LAT, ANCHORAGE_LON,
                       "tanker", 0.0),
                    _at_anchorage("111000002", "WAITING B")])
             for h in (0, 3, 6, 9)]
    assert dst.build_rendezvous_records(snaps) == []


def test_dark_gap_ignores_blackout_at_an_anchorage():
    """在錨地熄燈是等泊位 —— 關機的地點必須在外海才算數。"""
    coastal = [("2026-08-20T01:56:00+00:00", ANCHORAGE_LAT, ANCHORAGE_LON),
               ("2026-08-21T23:14:00+00:00", ANCHORAGE_LAT, ANCHORAGE_LON)]
    assert dst.find_dark_gaps(dst.build_vessel_timelines(
        _dark_snaps("777777777", "AT ANCHOR", coastal))) == []


def _rec(m1, m2, lat, lon):
    return {"vessel1": {"mmsi": m1}, "vessel2": {"mmsi": m2},
            "location": {"lat": lat, "lon": lon}}


def test_suppress_crowded_cells_drops_anchorages_keeps_events():
    """一格裡幾十組不同船對在「會合」＝錨地；單獨一組＝事件。

    離岸門檻擋不住外錨地（廈門外錨地離岸 15-20km），密度才分得開：
    實測該格 11 天內有 117 組不同船對，其餘每格最多 6 組。
    """
    anchorage = [_rec(f"41300{i:04d}", f"41400{i:04d}", 24.11, 118.30)
                 for i in range(10)]
    event = [_rec("620999315", "613002360", 21.60, 121.60)]
    kept, crowded = dst.suppress_crowded_cells(anchorage + event)
    assert crowded == 1
    assert [r["vessel1"]["mmsi"] for r in kept] == ["620999315"]


def test_suppress_crowded_cells_counts_pairs_not_records():
    """同一對船在同一格停好幾天會產生多筆紀錄 —— 那是一次事件，不是擁擠。"""
    repeated = [_rec("620999315", "613002360", 21.60, 121.60) for _ in range(20)]
    kept, crowded = dst.suppress_crowded_cells(repeated)
    assert crowded == 0 and len(kept) == 20
