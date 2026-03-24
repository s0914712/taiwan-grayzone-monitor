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

DATA_DIR = Path("data")
TRACK_HISTORY_FILE = DATA_DIR / "ais_track_history.json"
SNAPSHOT_FILE = DATA_DIR / "ais_snapshot.json"
OUTPUT_FILE = DATA_DIR / "ship_transfers.json"

# ── 門檻設定 ────────────────────────────────────────────
ALONGSIDE_DISTANCE_KM = 0.01       # 10 公尺
MAX_SPEED_KN = 5.0                 # 旁靠時速度 < 5 knots
PORT_EXCLUSION_KM = 2.0            # 港口排除半徑 2 公里
MIN_DURATION_HOURS = 1.0           # 至少旁靠 1 小時
PARALLEL_HEADING_DEG = 15          # 雙拖判定：航向差 < 15°
PAIR_TRAWL_SPEED_MIN = 2.0        # 雙拖速度下限
PAIR_TRAWL_SPEED_MAX = 6.0        # 雙拖速度上限

# ── 港口座標（商港 + 漁港）────────────────────────────────
PORTS = {
    # === 商港 Commercial Ports ===
    "高雄港 Kaohsiung":      (22.6153, 120.2664),
    "基隆港 Keelung":        (25.1509, 121.7405),
    "台中港 Taichung":       (24.2906, 120.5148),
    "台北港 Taipei":         (25.1580, 121.3728),
    "花蓮港 Hualien":        (23.9780, 121.6260),
    "蘇澳港 Suao":          (24.5946, 121.8622),
    "馬公港 Magong":         (23.5637, 119.5666),
    "金門料羅灣 Kinmen":     (24.4275, 118.3170),
    "馬祖福澳港 Matsu":     (26.1608, 119.9490),
    # === 漁港 Fishing Ports ===
    "安平港 Anping":         (22.9972, 120.1600),
    "永安漁港 Yongan":       (25.0030, 121.0130),
    "麥寮港 Mailiao":        (23.7500, 120.2500),
    "梧棲漁港 Wuqi":         (24.2950, 120.5180),
    "新竹南寮漁港 Nanliao":  (24.8280, 120.9290),
    "正濱漁港 Zhengbin":     (25.1480, 121.7520),
    "富基漁港 Fuji":         (25.2870, 121.5380),
    "淡水漁港 Tamsui":       (25.1790, 121.4280),
    "竹圍漁港 Zhuwei":       (25.1100, 121.2360),
    "東港漁港 Donggang":     (22.4640, 120.4410),
    "前鎮漁港 Qianzhen":    (22.5930, 120.3070),
    "南方澳漁港 Nanfangao": (24.5850, 121.8700),
    "大溪漁港 Daxi":         (24.9380, 121.9000),
    "成功漁港 Chenggong":    (23.0990, 121.3810),
    "富岡漁港 Fugang":       (22.7920, 121.1740),
    "後壁湖漁港 Houbihu":    (21.9460, 120.7440),
    "興達港 Xingda":         (22.8580, 120.2130),
    "布袋漁港 Budai":        (23.3730, 120.1600),
    "將軍漁港 Jiangjun":     (23.2050, 120.0900),
    "澎湖第三漁港 Penghu3":  (23.5560, 119.5620),
}

# ── 漁場定義（與 fetch_ais_data.py 一致）────────────────
FISHING_HOTSPOTS = {
    'taiwan_bank':   [[22.0, 117.0], [23.5, 119.5]],
    'penghu':        [[23.0, 119.0], [24.0, 120.0]],
    'kuroshio_east': [[22.5, 121.0], [24.5, 122.0]],
    'northeast':     [[24.8, 121.5], [25.8, 123.0]],
    'southwest':     [[22.0, 120.0], [23.0, 120.8]],
}


# ── 工具函式 ────────────────────────────────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    """兩點間距離（公里）"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def is_in_port(lat, lon):
    """檢查是否在任何港口排除區域內"""
    for name, (plat, plon) in PORTS.items():
        if haversine_km(lat, lon, plat, plon) < PORT_EXCLUSION_KM:
            return name
    return None


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


def process_track_history():
    """
    掃描 14 天 AIS 軌跡歷史，偵測持續旁靠事件
    """
    if not TRACK_HISTORY_FILE.exists():
        print("⚠️ 找不到 ais_track_history.json，跳過歷史分析")
        return []

    with open(TRACK_HISTORY_FILE, 'r', encoding='utf-8') as f:
        history = json.load(f)

    # Track history can be a list of snapshots or a dict with "snapshots" key
    if isinstance(history, list):
        snapshots = history
    else:
        snapshots = history.get("snapshots", [])
    if not snapshots:
        print("⚠️ ais_track_history.json 無快照資料")
        return []

    print(f"📊 載入 {len(snapshots)} 個歷史快照...")

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
        "updated_at": datetime.now(timezone.utc).isoformat() + "Z",
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

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ 旁靠偵測完成: {len(active_transfers)} 進行中, "
          f"{len(history_transfers)} 歷史, "
          f"{suspicious_count} 可疑, {trawling_count} 雙拖")
    print(f"📁 輸出: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
