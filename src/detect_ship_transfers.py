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

from fetch_ais_data import is_cn_fishing_vessel, is_gov_candidate
from geo_utils import NM_TO_KM, haversine_km
from geofence import (PORTS, CN_PORTS, PORT_EXCLUSION_KM, CN_PORT_EXCLUSION_KM,
                      is_in_port, is_offshore)
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

# ── 漂流會合門檻 Drift-rendezvous ───────────────────────
# ALONGSIDE_DISTANCE_KM 的 10 公尺是照漁船雙拖調的，對大船形同關閉偵測：
# 兩艘 330m VLCC 併靠時**中心點**就相距 50-80m，AIS 位置誤差本身也有上百公尺。
# 這一層補的是「大船在公海停下來碰面」，不預設已經併靠。
RENDEZVOUS_MAX_KM = 5.0            # 會合判定上限（10m–5km，10m 以下歸旁靠層）
RENDEZVOUS_MAX_SPEED_KN = 3.0      # 雙方近乎停俥
RENDEZVOUS_MIN_DURATION_HOURS = 3.0
COMMERCIAL_TYPES = {"tanker", "cargo", "lng"}
# 會合與關機兩層都只看離岸事件（公里）。
# 港口清單永遠補不完：實測 11 天的 tier-2，未設此門檻時「漂流會合」有 16,867 組，
# 光是閩江口（26.27/119.79）一處未列入 CN_PORTS 的錨地就佔了數千組，台中外錨地、
# 麥寮、台北港、基隆外海各再數百組 —— 全是在等泊位的船。把 CN 港口排除半徑放大到
# 25km 只降到 5,022 組，改量「離岸距離」則降到 224 組，而 MEDNA 的漂流區（離岸
# 中位數 44km）完好無損。錨地離岸 0.3–8km，灰區行為離岸數十公里，這條線分得開。
OFFSHORE_MIN_KM = 15.0
# 錨地抑制：同一格裡出現太多**不同船對**在「會合」，那是錨地，不是事件。
# 離岸門檻擋不住外錨地 —— 廈門外錨地（24.1/118.3）離岸 15-20km，幾何上與海上
# 過駁一模一樣。但密度分得開：實測 11 天裡那一格有 117 組不同船對，其餘每格
# 最多 6 組。這是 match_sar_ais.py 用重複性辨識固定設施的同一個想法。
ANCHORAGE_CELL_DEG = 0.1
ANCHORAGE_CELL_MIN_PAIRS = 8
# 身分不可信的船（純數字中國船名）不算商船，見 effective_type()
UNTRUSTED_ID_TYPE = "untrusted_id"

# ── 關機事件門檻 AIS blackout ───────────────────────────
DARK_MIN_GAP_HOURS = 18.0          # 與 analyze_suspicious 的 going-dark 同義
# 靜默超過這個長度就不再當成關機事件。`drift_kn` 是「兩端直線距離 ÷ 時數」，
# 那只有在缺口夠短時才約等於漂流速度：實測 tier-1 的視窗實際橫跨 632 小時，
# 一艘船在頭尾各出現一次就會得到 620 小時、位移 370km、「0.32 節」——
# 它其實跑了一趟東南亞，不是在原地漂。海上過駁本身是 12-30 小時的事，
# 72 小時已經給得很寬。
DARK_MAX_GAP_HOURS = 72.0
DARK_DRIFT_MAX_KN = 2.0            # 靜默期間平均位移速度低於此值＝關機後在漂
CODARK_MIN_OVERLAP_HOURS = 6.0     # 兩船靜默區間需重疊多久
CODARK_MAX_SEPARATION_KM = 30.0    # 兩船失去訊號時的最大相距
MAX_DARK_RECORDS = 200             # 輸出上限（依時長排序後截斷）
# 只對商船判關機。漁船停止播報再漂一天是日常作業 —— 實測放行全船隊會得到
# 19,532 段關機、330 萬組「共同關機」，那不是訊號，是把整支漁船隊兩兩配對。
# 影子船隊的關機才有意義，而那一定是 tanker/cargo/lng。要擴大範圍改這個集合。
DARK_WATCH_TYPES = COMMERCIAL_TYPES

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


def effective_type(v):
    """船型，但先用船名修正。

    AIS 的船型碼對中國船隊不可靠（見根目錄 CLAUDE.md「Survey-pattern false
    positives」）：福建拖網船大量播報成 `cargo`/`tanker`。不修正的話，
    「至少一方是商船」這個條件會被整支漁船隊通過 —— 實測未修正時漂流會合
    有 1,205 組「可疑」，前幾名全是 MINLIANYU/MINDONGYU 之類的拖網船兩兩配對。
    `analyze_suspicious.py` 用的是同一個名稱規則。

    純數字的中國船名（`60322`、`16888`、`00218`…）另外標成 `untrusted_id`：
    實測剩下的偽陽性幾乎全是這種船在福建漁場與 MINLIANYU 拖網船配對 ——
    那是漁獲運搬船在收魚，不是影子船隊的油。`fetch_ais_data.is_gov_candidate()`
    也是拿同一個條件標「身分待人工確認」，兩邊對這種識別碼的態度一致：
    不可信，所以不足以充當「至少一方是商船」裡的那個商船。
    """
    name = v.get("name") or ""
    if is_cn_fishing_vessel(name):
        return "fishing"
    if is_gov_candidate(name, v.get("mmsi")):
        return UNTRUSTED_ID_TYPE
    return v.get("type_name", "unknown")


def is_trackable(v):
    """共用前置過濾：座標有效、不是浮標/漁具、不在港內（含錨泊區）。

    港內排除放在這裡而非各偵測器內，是因為錨地裡幾十艘船本來就彼此相距
    數百公尺、速度為 0 —— 任何以距離為準的偵測若不先剔除港區，輸出會被
    錨泊船灌爆。
    """
    lat = v.get("lat")
    lon = v.get("lon")
    if lat is None or lon is None:
        return False
    vname = v.get("name", "") or ""
    if "%" in vname or "BUOY" in vname.upper():
        return False
    return not is_in_port(lat, lon)


def find_pairs_in_snapshot(vessels):
    """
    在單一快照中找出所有距離 < 10m 且速度 < 5kn 的船對
    使用 bounding box 預篩加速
    """
    pairs = []
    # 建立索引（排除港內船隻、無效座標、浮標/漁具）
    valid = []
    for v in vessels:
        speed = v.get("speed", 0) or 0
        if speed > MAX_SPEED_KN:
            continue
        if not is_trackable(v):
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


def find_rendezvous_in_snapshot(vessels):
    """單一快照中找出「漂流會合」船對：雙方近乎停俥、相距 10 公尺 — 5 公里。

    偽陽性控制有兩層，缺一不可：
      * 至少一方是商船（tanker/cargo/lng），且不得**雙方皆為漁船** ——
        5 公里的窗口放進漁場會把整支船隊兩兩配對。
      * 港區/錨泊區已由 is_trackable() 剔除。
    10 公尺以下不在這裡回報，交給 find_pairs_in_snapshot()（旁靠層），
    避免同一事件在輸出裡出現兩次。
    """
    valid = [v for v in vessels
             if (v.get("speed", 0) or 0) < RENDEZVOUS_MAX_SPEED_KN and is_trackable(v)
             and is_offshore(v["lat"], v["lon"], OFFSHORE_MIN_KM)]
    valid.sort(key=lambda v: v["lat"])

    pairs = []
    lat_window = RENDEZVOUS_MAX_KM / 111.0
    for i, v1 in enumerate(valid):
        lat1, lon1 = v1["lat"], v1["lon"]
        # 同緯度下經度一度較短，除以 cos(lat) 還原成經度差上限
        lon_window = lat_window / max(math.cos(math.radians(lat1)), 0.1)
        type1 = effective_type(v1)
        for v2 in valid[i + 1:]:
            lat2, lon2 = v2["lat"], v2["lon"]
            if lat2 - lat1 > lat_window:
                break
            if abs(lon2 - lon1) > lon_window:
                continue
            type2 = effective_type(v2)
            if type1 == "fishing" and type2 == "fishing":
                continue
            if type1 not in COMMERCIAL_TYPES and type2 not in COMMERCIAL_TYPES:
                continue
            dist = haversine_km(lat1, lon1, lat2, lon2)
            if ALONGSIDE_DISTANCE_KM <= dist <= RENDEZVOUS_MAX_KM:
                pair_key = tuple(sorted([str(v1.get("mmsi", "")), str(v2.get("mmsi", ""))]))
                pairs.append((pair_key, v1, v2, dist))
    return pairs


def suppress_crowded_cells(records, min_pairs=ANCHORAGE_CELL_MIN_PAIRS,
                           cell_deg=ANCHORAGE_CELL_DEG):
    """移除落在「錨地格」的紀錄，回傳 (保留的紀錄, 被判定為錨地的格數)。

    錨地格＝同一個 cell_deg 方格內出現 ≥min_pairs 組**不重複**船對。
    用不重複船對而非紀錄數，是因為同一對船在錨地一停數天會產生好幾筆紀錄，
    用紀錄數會把一次真實事件的多筆觀測也算成擁擠。
    """
    cells = {}
    for r in records:
        loc = r.get("location") or {}
        if loc.get("lat") is None or loc.get("lon") is None:
            continue
        key = (round(loc["lat"] / cell_deg), round(loc["lon"] / cell_deg))
        pair = tuple(sorted([r["vessel1"]["mmsi"], r["vessel2"]["mmsi"]]))
        cells.setdefault(key, set()).add(pair)
    crowded = {k for k, pairs in cells.items() if len(pairs) >= min_pairs}
    kept = []
    for r in records:
        loc = r.get("location") or {}
        if loc.get("lat") is None or loc.get("lon") is None:
            kept.append(r)
            continue
        if (round(loc["lat"] / cell_deg), round(loc["lon"] / cell_deg)) in crowded:
            continue
        kept.append(r)
    return kept, len(crowded)


def build_vessel_timelines(snapshots):
    """{mmsi: [{t, lat, lon, speed, name, type_name}, …]}，依時間排序。

    浮標/漁具與港內點一律剔除：關機偵測問的是「這艘船在海上消失了嗎」，
    停港熄燈不是訊號。
    """
    timelines = {}
    for snap in snapshots:
        ts = snap.get("timestamp", "")
        if not ts:
            continue
        for v in snap.get("vessels", []):
            if not is_trackable(v):
                continue
            mmsi = str(v.get("mmsi", ""))
            if not mmsi:
                continue
            timelines.setdefault(mmsi, []).append({
                "t": ts, "lat": v["lat"], "lon": v["lon"],
                "speed": v.get("speed", 0) or 0,
                "name": v.get("name", ""),
                "type_name": effective_type(v),
            })
    for pts in timelines.values():
        pts.sort(key=lambda p: p["t"])
    return timelines


def _hours_between(t1, t2):
    """兩個 ISO 時戳相差幾小時；無法解析回傳 None。"""
    try:
        a = datetime.fromisoformat(t1.replace('Z', '+00:00'))
        b = datetime.fromisoformat(t2.replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError):
        return None
    return (b - a).total_seconds() / 3600


def find_dark_gaps(timelines, min_gap_hours=DARK_MIN_GAP_HOURS,
                   types=DARK_WATCH_TYPES, max_gap_hours=DARK_MAX_GAP_HOURS):
    """每艘船的 AIS 靜默區間（關機事件）。

    只取**後來又出現**的靜默 —— 軌跡尾端沒有下一點的船是駛出監測範圍，
    不是關機。每段附上靜默期間的平均位移速度 `drift_kn`：
    關機趕路（數節）與關機後原地漂（<2kn）是完全不同的行為，後者正是
    海上過駁該有的樣子。

    types=None 可關掉船型過濾（供分析用）；預設只看 DARK_WATCH_TYPES。
    缺口長於 max_gap_hours 的不算關機 —— 那是「這段期間沒看到這艘船」，
    見 DARK_MAX_GAP_HOURS 的說明。
    """
    gaps = []
    for mmsi, pts in timelines.items():
        if types is not None and not any(
                p.get("type_name") in types for p in pts):
            continue
        for a, b in zip(pts, pts[1:]):
            hours = _hours_between(a["t"], b["t"])
            if hours is None or not min_gap_hours <= hours <= max_gap_hours:
                continue
            # 失去訊號的地點必須在外海 —— 港外錨地熄燈是等泊位，不是關機
            if not is_offshore(a["lat"], a["lon"], OFFSHORE_MIN_KM):
                continue
            dist = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
            gaps.append({
                "mmsi": mmsi,
                "name": b.get("name") or a.get("name", ""),
                "type_name": b.get("type_name", "unknown"),
                "start": a["t"], "end": b["t"],
                "gap_hours": round(hours, 1),
                "last_lat": a["lat"], "last_lon": a["lon"],
                "resume_lat": b["lat"], "resume_lon": b["lon"],
                "distance_km": round(dist, 1),
                "drift_kn": round(dist / hours / NM_TO_KM, 2) if hours else None,
            })
    gaps.sort(key=lambda g: g["start"])
    return gaps


def is_drifting_gap(gap):
    """靜默期間是否幾乎沒移動。

    `drift_kn` 為 0.0 —— 原地不動，最像過駁的情況 —— 是 falsy，
    不能用 `gap["drift_kn"] or 預設值` 判斷，那會把它算成「沒在漂」。
    """
    kn = gap.get("drift_kn")
    return kn is not None and kn < DARK_DRIFT_MAX_KN


def find_codark_pairs(gaps, min_overlap_hours=CODARK_MIN_OVERLAP_HOURS,
                      max_separation_km=CODARK_MAX_SEPARATION_KM,
                      max_pairs=MAX_DARK_RECORDS * 10):
    """兩艘船在同一時段、同一海域一起失去訊號。

    海上過駁最乾淨的作法是雙方都關機 —— 只要有一方還在播報，AIS 上就看得到
    一艘船停在另一艘不存在的船旁邊。共同關機補的正是「兩邊都不播報」這個
    盲區：兩段靜默區間重疊 ≥6h，且失去訊號時相距 ≤30km。

    gaps 需依 start 排序（find_dark_gaps 已排好）；以掃描線只比對可能重疊的
    區間，避免 O(n²) 全比。
    """
    pairs = []
    for i, g1 in enumerate(gaps):
        for g2 in gaps[i + 1:]:
            # gaps 依 start 排序：g2 起點已晚於 g1 終點就不可能再重疊
            if g2["start"] >= g1["end"]:
                break
            if g1["mmsi"] == g2["mmsi"]:
                continue
            overlap = _hours_between(g2["start"], min(g1["end"], g2["end"]))
            if overlap is None or overlap < min_overlap_hours:
                continue
            sep = haversine_km(g1["last_lat"], g1["last_lon"],
                               g2["last_lat"], g2["last_lon"])
            if sep > max_separation_km:
                continue
            pairs.append({
                "vessel1": {k: g1[k] for k in
                            ("mmsi", "name", "type_name", "start", "end",
                             "gap_hours", "drift_kn")},
                "vessel2": {k: g2[k] for k in
                            ("mmsi", "name", "type_name", "start", "end",
                             "gap_hours", "drift_kn")},
                "overlap_hours": round(overlap, 1),
                "separation_km": round(sep, 1),
                "location": {"lat": round((g1["last_lat"] + g2["last_lat"]) / 2, 4),
                             "lon": round((g1["last_lon"] + g2["last_lon"]) / 2, 4)},
                "both_drifting": is_drifting_gap(g1) and is_drifting_gap(g2),
            })
            # 安全閥：船型過濾之外再擋一層，異常資料不至於把輸出撐爆
            if len(pairs) >= max_pairs:
                print(f"⚠️ 共同關機配對達上限 {max_pairs}，停止掃描")
                pairs.sort(key=lambda p: (-p["overlap_hours"], p["separation_km"]))
                return pairs
    pairs.sort(key=lambda p: (-p["overlap_hours"], p["separation_km"]))
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


def process_track_history(snapshots=None, finder=None):
    """
    掃描 tier-1 + tier-2 合併軌跡歷史，偵測持續的成對事件。
    合併兩層是為了涵蓋油輪↔油輪的海上轉油（影子船隊），詳見
    load_merged_snapshots()。

    finder: 單一快照的配對函式，預設 find_pairs_in_snapshot（旁靠層）。
        傳入 find_rendezvous_in_snapshot 即以同一套「連續出現＝同一事件」
        的追蹤邏輯產出漂流會合事件。
    snapshots: 已載入的合併快照；省略時自行載入（呼叫端已載入時不必重讀）。
    """
    if snapshots is None:
        snapshots = load_merged_snapshots()
    if finder is None:
        finder = find_pairs_in_snapshot
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
        for pair_key, v1, v2, dist in finder(vessels):
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


def build_rendezvous_records(snapshots):
    """漂流會合事件 → 輸出紀錄（沿用 classify_transfer 的風險分數體系）。"""
    records = []
    for ev in process_track_history(snapshots, finder=find_rendezvous_in_snapshot):
        duration = estimate_duration_hours(
            ev["first_seen"], ev["last_seen"], ev["snapshot_count"])
        if duration < RENDEZVOUS_MIN_DURATION_HOURS:
            continue
        v1 = build_vessel_info(ev["v1_last"])
        v2 = build_vessel_info(ev["v2_last"])
        avg_lat = (v1["lat"] + v2["lat"]) / 2 if v1["lat"] and v2["lat"] else None
        avg_lon = (v1["lon"] + v2["lon"]) / 2 if v1["lon"] and v2["lon"] else None
        in_hotspot = is_in_fishing_hotspot(avg_lat, avg_lon) if avg_lat else None
        classification, risk_score, risk_factors = classify_transfer(
            ev["v1_last"], ev["v2_last"], duration, in_hotspot)
        records.append({
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
            "active": bool(ev.get("active")),
        })
    records, crowded = suppress_crowded_cells(records)
    if crowded:
        print(f"  ⚓ 漂流會合：{crowded} 個錨地格已抑制")
    records.sort(key=lambda r: (-r["risk_score"], r["min_distance_m"]))
    return records


def build_dark_records(snapshots):
    """關機事件：共同關機船對 + 單船「關機後在漂」。

    單船那一項不是配對事件，但它是本模組唯一抓得到 MEDNA 型態的東西 ——
    對象船若從頭到尾沒播報過，AIS 裡永遠不會有第二艘船可以配。能留下的
    證據只有「這艘船關掉 AIS，然後以 0.7 節漂了 45 小時」這件事本身，
    而那正是把關機趕路與海上過駁分開的那條線。
    """
    timelines = build_vessel_timelines(snapshots)
    gaps = find_dark_gaps(timelines)
    codark, crowded = suppress_crowded_cells(find_codark_pairs(gaps))
    if crowded:
        print(f"  ⚓ 共同關機：{crowded} 個錨地格已抑制")
    drifting = [g for g in gaps if is_drifting_gap(g)]
    drifting.sort(key=lambda g: -g["gap_hours"])
    return {
        "codark_pairs": codark[:MAX_DARK_RECORDS],
        "dark_drift": drifting[:MAX_DARK_RECORDS],
        "params": {
            "min_gap_hours": DARK_MIN_GAP_HOURS,
            "drift_max_kn": DARK_DRIFT_MAX_KN,
            "codark_min_overlap_hours": CODARK_MIN_OVERLAP_HOURS,
            "codark_max_separation_km": CODARK_MAX_SEPARATION_KM,
        },
        "_totals": {"dark_gaps": len(gaps), "codark_pairs": len(codark),
                    "dark_drift": len(drifting)},
    }


def main():
    print("🚢 海上旁靠偵測開始...")

    snapshots = load_merged_snapshots()
    events = process_track_history(snapshots)
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

    # ── 漂流會合 & 關機事件（旁靠層以外的兩種過駁跡象）──
    rendezvous = build_rendezvous_records(snapshots)
    dark = build_dark_records(snapshots)
    dark_totals = dark.pop("_totals")
    print(f"🛟 漂流會合 {len(rendezvous)} 組；"
          f"關機區間 {dark_totals['dark_gaps']} 段 → "
          f"共同關機 {dark_totals['codark_pairs']} 組、"
          f"關機漂流 {dark_totals['dark_drift']} 艘次")

    output = {
        # tz-aware isoformat() already carries +00:00; appending 'Z' makes it
        # unparseable by JS Date()
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "active_transfers": active_transfers,
        "history": history_transfers,
        "drift_rendezvous": rendezvous,
        "dark_events": dark,
        "summary": {
            "active_count": len(active_transfers),
            "history_count": len(history_transfers),
            "suspicious_count": suspicious_count,
            "pair_trawling_count": trawling_count,
            "unique_vessels": len(unique_mmsis),
            "history_days": 14,
            "drift_rendezvous_count": len(rendezvous),
            "codark_pair_count": dark_totals["codark_pairs"],
            "dark_drift_count": dark_totals["dark_drift"],
        }
    }

    atomic_write_json(OUTPUT_FILE, output)

    print(f"✅ 旁靠偵測完成: {len(active_transfers)} 進行中, "
          f"{len(history_transfers)} 歷史, "
          f"{suspicious_count} 可疑, {trawling_count} 雙拖")
    print(f"📁 輸出: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
