"""STS 偵測 tier-2 合併修正的回歸測試 — detect_ship_transfers.py

驗證核心缺口已修：偵測器現在合併 tier-1（漁船/公務船）+ tier-2（商船/油輪），
才能抓到**油輪↔油輪的海上轉油（影子船隊）**——先前只讀 tier-1，tanker-to-tanker
STS 永遠偵測不到。
"""
import json

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
