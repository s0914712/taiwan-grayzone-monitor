#!/usr/bin/env python3
"""
================================================================================
海上旁靠偵測 — Ship-to-Ship Transfer Detection
Detect vessels alongside each other (< 10m) for 1+ hour, excluding ports.
Classify as pair trawling vs. suspicious transfer.
================================================================================
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from geo_utils import haversine_km
from geofence import PORTS, CN_PORTS, PORT_EXCLUSION_KM, CN_PORT_EXCLUSION_KM, is_in_port
from io_utils import atomic_write_json

DATA_DIR = Path("data")
DOCS_DIR = Path("docs")
# tier-1 軌跡歷史已搬至 docs/；tier-2（商船/油輪/身分變更船）在 data/
TRACK_HISTORY_FILE = DOCS_DIR / "ais_track_history.json"       # tier-1: 漁船/公務船
TRACK_COMMERCIAL_FILE = DATA_DIR / "ais_track_commercial.json"  # tier-2: cargo/tanker/lng
SNAPSHOT_FILE = DATA_DIR / "ais_snapshot.json"
OUTPUT_FILE = DATA_DIR / "ship_transfers.json"

# ── 門檻設定 ────────────────────────────────────────────
ALONGSIDE_DISTANCE_KM = 0.01       # 10 公尺
MAX_SPEED_KN = 5.0                 # 旁靠時速度 < 5 knots
MIN_DURATION_HOURS = 1.0           # 至少旁靠 1 小時
PARALLEL_HEADING_DEG = 15          # 雙拖判定：航向差 < 15°
PAIR_TRAWL_SPEED_MIN = 2.0        # 雙拖速度下限
PAIR_TRAWL_SPEED_MAX = 6.0        # 雙拖速度上限

# ── 漁場定義（與 fetch_ais_data.py 一致）────────────────
FISHING_HOTSPOTS = {
    'taiwan_bank':   [[22.0, 117.0], [23.5, 119.5]],
    'penghu':        [[23.0, 119.0], [24.0, 120.0]],
    'kuroshio_east': [[22.5, 121.0], [24.5, 122.0]],
    'northeast':     [[24.8, 121.5], [25.8, 123.0]],
    'southwest':     [[22.0, 120.0], [23.0, 120.8]],
}


# ── 工具函式 ────────────────────────────────────────────



def is_in_fishing_hotspot(lat, lon):
    """檢查是否在漁場範圍內"""
    for name, bounds in FISHING_HOTSPOTS.items():
        if (bounds[0][0] <= lat <= bounds[1][0] and
                bounds[0][1] <= lon <= bounds[1][1]):
            return name
    return None


def heading_diff(h1, h2):
    """兩航向差的絕對值（0-180°）"""
    if h1 is None or h2 is None:
        return 180
    d = abs(h1 - h2) % 360
    return d if d <= 180 else 360 - d


def classify_transfer(v1, v2, duration_hours, in_hotspot):
    """
    分類旁靠事件並計算風險分數
    回傳: (classification, risk_score, risk_factors)
    """
    score = 0
    factors = []

    type1 = v1.get("type_name", "unknown")
    type2 = v2.get("type_name", "unknown")
    speed1 = v1.get("speed", 0) or 0
    speed2 = v2.get("speed", 0) or 0
    heading1 = v1.get("heading")
    heading2 = v2.get("heading")
    mmsi1 = str(v1.get("mmsi", ""))
    mmsi2 = str(v2.get("mmsi", ""))

    # 不同船型
    if type1 != type2:
        score += 30
        factors.append("different_types")

    # 雙方近乎靜止
    if speed1 < 1 and speed2 < 1:
        score += 15
        factors.append("stationary")

    # 非漁場內
    if not in_hotspot:
        score += 15
        factors.append("outside_hotspot")

    # 旁靠超過 3 小時
    if duration_hours > 3:
        score += 10
        factors.append("long_duration")

    # 外國籍船舶（台灣 MMSI 以 416 開頭）
    tw_flag = mmsi1.startswith("416") or mmsi1.startswith("419")
    foreign1 = not (mmsi1.startswith("416") or mmsi1.startswith("419"))
    foreign2 = not (mmsi2.startswith("416") or mmsi2.startswith("419"))
    if foreign1 or foreign2:
        score += 10
        factors.append("foreign_flag")

    # 雙拖減分：雙方都是漁船、平行航向、在漁場內、速度 2-6kn
    both_fishing = type1 == "fishing" and type2 == "fishing"
    parallel = heading_diff(heading1, heading2) < PARALLEL_HEADING_DEG
    both_moving = (PAIR_TRAWL_SPEED_MIN <= speed1 <= PAIR_TRAWL_SPEED_MAX and
                   PAIR_TRAWL_SPEED_MIN <= speed2 <= PAIR_TRAWL_SPEED_MAX)
    if both_fishing and parallel and in_hotspot and both_moving:
        score -= 30
        factors.append("pair_trawling_pattern")

    score = max(0, min(100, score))

    if score >= 40:
        classification = "suspicious"
    elif score < 20 and both_fishing:
        classification = "pair_trawling"
    else:
        classification = "normal"

    return classification, score, factors


def find_pairs_in_snapshot(vessels):
    """
    在單一快照中找出所有距離 < 10m 且速度 < 5kn 的船對
    使用 bounding box 預篩加速
    """
    pairs = []
    n = len(vessels)
    # 建立索引（排除港內船隻、無效座標、浮標/漁具）
    valid = []
    for v in vessels:
        lat = v.get("lat")
        lon = v.get("lon")
        if lat is None or lon is None:
            continue
        # 排除浮標與漁具（名稱含 % 或 BUOY）
        vname = v.get("name", "") or ""
        if "%" in vname or "BUOY" in vname.upper():
            continue
        speed = v.get("speed", 0) or 0
        if speed > MAX_SPEED_KN:
            continue
        if is_in_port(lat, lon):
            continue
        valid.append(v)

    # 按緯度排序後用 bounding box 快速篩選
    valid.sort(key=lambda v: v["lat"])
    deg_threshold = 0.001  # ~110m 的緯度，寬鬆篩選

    for i in range(len(valid)):
        v1 = valid[i]
        lat1, lon1 = v1["lat"], v1["lon"]
        for j in range(i + 1, len(valid)):
            v2 = valid[j]
            lat2, lon2 = v2["lat"], v2["lon"]
            # 緯度快速排除
            if lat2 - lat1 > deg_threshold:
                break
            # 經度快速排除
            if abs(lon2 - lon1) > deg_threshold:
                continue
            # 精確距離
            dist = haversine_km(lat1, lon1, lat2, lon2)
            if dist < ALONGSIDE_DISTANCE_KM:
                pair_key = tuple(sorted([str(v1.get("mmsi", "")), str(v2.get("mmsi", ""))]))
                pairs.append((pair_key, v1, v2, dist))

    return pairs


def _load_snapshots(path, label):
    """載入單一軌跡檔（list 或 {snapshots:[...]}），回傳快照 list。"""
    if not path.exists():
        print(f"⚠️ 找不到 {label} ({path})，略過該層")
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"⚠️ 讀取 {label} 失敗: {e}")
        return []
    return data if isinstance(data, list) else data.get("snapshots", [])


def load_merged_snapshots():
    """合併 tier-1（漁船/公務船）+ tier-2（商船/油輪/身分變更船）軌跡快照。

    以**精確 timestamp**為鍵，把同一時刻兩層的 vessels 併進同一份快照，
    find_pairs_in_snapshot 才能偵測**跨層旁靠**——尤其
    **油輪↔油輪 = 影子船隊海上轉油**。僅讀 tier-1 時，商船軌跡完全不進偵測，
    tanker-to-tanker STS 永遠抓不到（本次修正的核心）。

    tier-1 與 tier-2 由 fetch_ais_data 於同一次執行用同一個 now_str 寫入，
    故 timestamp 精確對齊。**不可用 period_key 當鍵** —— 同一 2h 時段的
    重跑會產生多筆相同 period_key、但船已移動的快照，用 period_key 合併會把
    不同時刻的位置併在一起，灌出假配對。

    兩層 MMSI 互斥（fetch_ais_data 將已在 tier-1 的 MMSI 排除於 tier-2），
    合併不需去重。
    """
    tier1 = _load_snapshots(TRACK_HISTORY_FILE, "tier-1 ais_track_history.json")
    tier2 = _load_snapshots(TRACK_COMMERCIAL_FILE, "tier-2 ais_track_commercial.json")

    merged = {}   # timestamp -> {"timestamp", "vessels": [...]}
    for snaps in (tier1, tier2):
        for s in snaps:
            key = s.get("timestamp") or s.get("period_key", "")
            if not key:
                continue
            slot = merged.setdefault(key, {"timestamp": key, "vessels": []})
            slot["vessels"].extend(s.get("vessels", []))

    result = sorted(merged.values(), key=lambda s: s.get("timestamp", ""))
    print(f"📊 合併軌跡快照: {len(result)} 份 "
          f"(tier-1 {len(tier1)} + tier-2 {len(tier2)} 筆)")
    return result


def process_track_history():
    """
    掃描 tier-1 + tier-2 合併軌跡歷史，偵測持續旁靠事件。
    合併兩層是為了涵蓋油輪↔油輪的海上轉油（影子船隊），詳見
    load_merged_snapshots()。
    """
    snapshots = load_merged_snapshots()
    if not snapshots:
        print("⚠️ 無軌跡快照資料（tier-1/tier-2 皆缺）")
        return []

    # 追蹤每對船的連續旁靠
    # active_pairs: {pair_key: {first_seen, last_seen, snapshots, v1_last, v2_last, min_dist}}
    active_pairs = {}
    completed_events = []

    for snap_idx, snap in enumerate(snapshots):
        ts = snap.get("timestamp", "")
        vessels = snap.get("vessels", [])
        if not vessels:
            continue

        current_pairs = {}
        for pair_key, v1, v2, dist in find_pairs_in_snapshot(vessels):
            current_pairs[pair_key] = (v1, v2, dist)

        # 更新追蹤中的 pairs
        for pk in list(active_pairs.keys()):
            if pk in current_pairs:
                v1, v2, dist = current_pairs[pk]
                active_pairs[pk]["last_seen"] = ts
                active_pairs[pk]["snapshot_count"] += 1
                active_pairs[pk]["v1_last"] = v1
                active_pairs[pk]["v2_last"] = v2
                active_pairs[pk]["min_dist"] = min(active_pairs[pk]["min_dist"], dist)
            else:
                # 旁靠結束
                ev = active_pairs.pop(pk)
                completed_events.append(ev)

        # 新增新 pairs
        for pk, (v1, v2, dist) in current_pairs.items():
            if pk not in active_pairs:
                active_pairs[pk] = {
                    "pair_key": pk,
                    "first_seen": ts,
                    "last_seen": ts,
                    "snapshot_count": 1,
                    "v1_first": v1,
                    "v2_first": v2,
                    "v1_last": v1,
                    "v2_last": v2,
                    "min_dist": dist,
                }

    # 將仍在進行中的 pairs 也加入（標記為 active）
    for pk, ev in active_pairs.items():
        ev["active"] = True
        completed_events.append(ev)

    return completed_events


def estimate_duration_hours(first_seen, last_seen, snapshot_count):
    """估算旁靠持續時間"""
    try:
        t1 = datetime.fromisoformat(first_seen.replace('Z', '+00:00'))
        t2 = datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
        diff = (t2 - t1).total_seconds() / 3600
        if diff > 0:
            return round(diff, 1)
    except (ValueError, TypeError):
        pass
    # 無法解析時間時，用快照數量估算（每快照約 2 小時）
    return round(max(0, (snapshot_count - 1)) * 2, 1)


def build_vessel_info(v):
    """提取船舶資訊"""
    return {
        "mmsi": str(v.get("mmsi", "")),
        "name": v.get("name", ""),
        "type_name": v.get("type_name", "unknown"),
        "lat": v.get("lat"),
        "lon": v.get("lon"),
        "speed": v.get("speed", 0),
        "heading": v.get("heading"),
    }


def main():
    print("🚢 海上旁靠偵測開始...")

    events = process_track_history()
    print(f"📋 偵測到 {len(events)} 組旁靠事件（含不足 1 小時）")

    # 過濾 & 分類
    active_transfers = []
    history_transfers = []

    for ev in events:
        duration = estimate_duration_hours(
            ev["first_seen"], ev["last_seen"], ev["snapshot_count"]
        )
        if duration < MIN_DURATION_HOURS:
            continue

        v1 = build_vessel_info(ev["v1_last"])
        v2 = build_vessel_info(ev["v2_last"])
        avg_lat = (v1["lat"] + v2["lat"]) / 2 if v1["lat"] and v2["lat"] else None
        avg_lon = (v1["lon"] + v2["lon"]) / 2 if v1["lon"] and v2["lon"] else None

        in_hotspot = is_in_fishing_hotspot(avg_lat, avg_lon) if avg_lat else None
        classification, risk_score, risk_factors = classify_transfer(
            ev["v1_last"], ev["v2_last"], duration, in_hotspot
        )

        record = {
            "first_seen": ev["first_seen"],
            "last_seen": ev["last_seen"],
            "duration_hours": duration,
            "vessel1": v1,
            "vessel2": v2,
            "min_distance_m": round(ev["min_dist"] * 1000, 1),
            "location": {"lat": avg_lat, "lon": avg_lon},
            "classification": classification,
            "risk_score": risk_score,
            "risk_factors": risk_factors,
        }

        if ev.get("active"):
            active_transfers.append(record)
        else:
            history_transfers.append(record)

    # 也檢查當前快照
    if SNAPSHOT_FILE.exists():
        with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
            snap = json.load(f)
        vessels = snap.get("vessels", [])
        current_pairs = find_pairs_in_snapshot(vessels)
        # 標記當前快照中的 pairs（即時狀態，不需 1h 門檻）
        for pair_key, v1, v2, dist in current_pairs:
            pk = tuple(sorted([str(v1.get("mmsi", "")), str(v2.get("mmsi", ""))]))
            # 檢查是否已在 active_transfers 中
            already = any(
                tuple(sorted([t["vessel1"]["mmsi"], t["vessel2"]["mmsi"]])) == pk
                for t in active_transfers
            )
            if not already:
                vi1 = build_vessel_info(v1)
                vi2 = build_vessel_info(v2)
                avg_lat = (vi1["lat"] + vi2["lat"]) / 2 if vi1["lat"] and vi2["lat"] else None
                avg_lon = (vi1["lon"] + vi2["lon"]) / 2 if vi1["lon"] and vi2["lon"] else None
                in_hotspot = is_in_fishing_hotspot(avg_lat, avg_lon) if avg_lat else None
                classification, risk_score, risk_factors = classify_transfer(v1, v2, 0, in_hotspot)
                active_transfers.append({
                    "first_seen": snap.get("updated_at", ""),
                    "last_seen": snap.get("updated_at", ""),
                    "duration_hours": 0,
                    "vessel1": vi1,
                    "vessel2": vi2,
                    "min_distance_m": round(dist * 1000, 1),
                    "location": {"lat": avg_lat, "lon": avg_lon},
                    "classification": classification,
                    "risk_score": risk_score,
                    "risk_factors": risk_factors,
                })

    # 排序：可疑優先，再按風險分數
    active_transfers.sort(key=lambda x: (-x["risk_score"], x["first_seen"]))
    history_transfers.sort(key=lambda x: (-x["risk_score"], x["first_seen"]))

    # 統計
    all_events = active_transfers + history_transfers
    unique_mmsis = set()
    for t in all_events:
        unique_mmsis.add(t["vessel1"]["mmsi"])
        unique_mmsis.add(t["vessel2"]["mmsi"])

    suspicious_count = sum(1 for t in all_events if t["classification"] == "suspicious")
    trawling_count = sum(1 for t in all_events if t["classification"] == "pair_trawling")

    output = {
        # tz-aware isoformat() already carries +00:00; appending 'Z' makes it
        # unparseable by JS Date()
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "active_transfers": active_transfers,
        "history": history_transfers,
        "summary": {
            "active_count": len(active_transfers),
            "history_count": len(history_transfers),
            "suspicious_count": suspicious_count,
            "pair_trawling_count": trawling_count,
            "unique_vessels": len(unique_mmsis),
            "history_days": 14,
        }
    }

    atomic_write_json(OUTPUT_FILE, output)

    print(f"✅ 旁靠偵測完成: {len(active_transfers)} 進行中, "
          f"{len(history_transfers)} 歷史, "
          f"{suspicious_count} 可疑, {trawling_count} 雙拖")
    print(f"📁 輸出: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
