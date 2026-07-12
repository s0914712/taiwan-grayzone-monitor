#!/usr/bin/env python3
"""
================================================================================
SAR × 本地 AIS 重比對 — Local AIS re-matching for GFW dark detections
================================================================================

GFW 的 matched/unmatched 標記是用他們自己的 AIS 來源做的；航港局 AIS 在
台海近岸密度更高。本模組把 GFW 標為 unmatched（暗船）的 SAR 偵測點，
拿本地 AIS 軌跡重新比對一次，刪掉假暗船，並產出可直接分析的輸出層。

方法（對應 CSIS/學界 SAR-AIS matching 慣例）：
1. **固定設施過濾** — 風機、養殖平台常被 SAR 偵測成船。除可選的
   data/fixed_infrastructure.json 靜態遮罩外，內建「重現性啟發式」：
   同一 0.01° 網格在資料窗內 ≥ INFRA_MIN_DATES 個不同日期都有暗偵測
   → 判定為固定設施（船不會天天停在同個 SAR 網格）。
2. **時間內插（dead reckoning）** — SAR 成像是瞬時的、AIS 回報有間隔
   （本管線 2h 快照）。比對前先把 AIS 軌跡推算到 SAR 過境時刻：
   兩側都有點→時間加權內插；只有單側→以航速航向做航位推算。
   GFW SAR 為 Sentinel-1，未帶 timestamp 時以台灣經度的固定過境窗
   （ascending ≈ 09:50 UTC、descending ≈ 21:55 UTC）當候選成像時刻。
3. **動態匹配半徑** — 門檻 = 基礎誤差（SAR 定位誤差 + 4wings HIGH 網格
   量化誤差 + AIS 定位誤差）+ 機動不確定性（航速 × 時距 × 係數），
   夾在 [MIN_GATE_KM, MAX_GATE_KM]；不用固定半徑。
4. **一對一指派** — 多船密集區以每個過境（date × pass）為單位做
   偵測點↔船舶的指派：有 scipy 用匈牙利演算法（linear_sum_assignment），
   否則退回全域成本排序的貪婪最近鄰 + gating，避免一對多誤配。
5. **船長交叉驗證** — 偵測點帶 SAR 估計船長且 AIS 側查得到登記船長時，
   差異過大標記 length_mismatch（AIS 身分冒用線索）。目前的
   sar-presence 日彙整資料集不帶船長，此檢查在資料到位前自然為空。
6. **輸出層** — 殘餘暗船 0.1° 密度網格（熱圖用）+ 按海域法域
   （12浬 / 24浬鄰接區 / EEZ）與方位子區域的每日時間序列
   （raw 與 screened 兩條，可直接接 changepoint detection）。

輸入：
  data/sar_detections.json       全量暗偵測點（fetch_gfw_data.py 產出）
                                 缺檔時退回 dark_vessels.json 的 dark_details
  docs/ais_track_history.json    tier-1 AIS 軌跡（14 天）
  data/ais_track_commercial.json tier-2 AIS 軌跡（28 天；退回 docs/ 舊位置）
  data/ais_snapshot.json         最新 AIS 快照（補最後位置）
  data/fixed_infrastructure.json 可選固定設施遮罩
                                 [{"lat","lon","radius_km"?}, ...] 或
                                 {"points":[...]}（例如 GFW fixed
                                 infrastructure 資料集匯出）

輸出：data/sar_ais_matches.json
================================================================================
"""

import math
import re
import sys
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from geo_utils import haversine_km
from io_utils import atomic_write_json, load_json
import geofence

# =============================================================================
# 設定
# =============================================================================

DATA_DIR = Path("data")
DOCS_DIR = Path("docs")

SAR_DETECTIONS_PATH = DATA_DIR / 'sar_detections.json'
DARK_VESSELS_PATH = DATA_DIR / 'dark_vessels.json'          # 降級來源
TIER1_PATH = DOCS_DIR / 'ais_track_history.json'
TIER2_PATHS = (DATA_DIR / 'ais_track_commercial.json',
               DOCS_DIR / 'ais_track_commercial.json')       # 後者為舊位置
SNAPSHOT_PATH = DATA_DIR / 'ais_snapshot.json'
FIXED_INFRA_PATH = DATA_DIR / 'fixed_infrastructure.json'
OUTPUT_PATH = DATA_DIR / 'sar_ais_matches.json'

# Sentinel-1 過境窗（UTC 時 · 分）：台灣經度（~120°E，LST≈UTC+8）
#   ascending  節點 18:00 LST → ~09:50 UTC
#   descending 節點 06:00 LST → ~21:55 UTC（同一 UTC 日）
S1_PASS_TIMES_UTC = (
    ('ascending', 9, 50),
    ('descending', 21, 55),
)

# 誤差模型（公里）
SAR_POS_ERROR_KM = 0.5        # Sentinel-1 幾何定位誤差（數百公尺級）
GRID_QUANT_ERROR_KM = 0.8     # 4wings HIGH（0.01°）網格中心量化誤差（半格對角）
AIS_POS_ERROR_KM = 0.1        # AIS/GNSS 定位誤差
BASE_ERROR_KM = SAR_POS_ERROR_KM + GRID_QUANT_ERROR_KM + AIS_POS_ERROR_KM

# 動態 gate：BASE + 機動不確定性(航速×時距×係數)，夾在 [MIN, MAX]
MIN_GATE_KM = 1.5
MAX_GATE_KM = 10.0
UNC_FRAC_INTERPOLATED = 0.35  # 兩側內插：偏差主要來自轉向，取較小係數
UNC_FRAC_DEAD_RECKON = 0.5    # 單側航位推算：航向/航速漂移
UNC_FRAC_UNKNOWN_HDG = 1.0    # 航向不明的移動船：整段航程都是不確定圈
STATIONARY_UNC_KM = 0.3       # 錨泊/靜止船的迴旋半徑
STATIONARY_SPEED_KN = 0.5

MAX_DT_HOURS = 3.0            # AIS 點與 SAR 時刻的最大時距（超過不推算）
KN_TO_KMH = 1.852

# 固定設施重現性啟發式
INFRA_CELL_DEG = 0.01         # 與 4wings HIGH 網格一致
INFRA_MIN_DATES = 6           # 同 cell ≥6 個不同日期有暗偵測 → 固定設施
FIXED_INFRA_MASK_KM = 1.0     # 靜態遮罩點的預設半徑

# 輸出層
DENSITY_CELL_DEG = 0.1        # 熱圖密度網格
CANDIDATE_CELL_DEG = 0.3      # 候選船空間索引網格（>MAX_GATE_KM×2）
MAX_REMATCH_DETAILS = 500
MAX_RESIDUAL_DETAILS = 1200
MAX_INFRA_DETAILS = 400

# 船長交叉驗證
LENGTH_MISMATCH_FRAC = 0.35   # 相對差 >35%
LENGTH_MISMATCH_MIN_M = 30.0  # 且絕對差 >30m 才標記

ZONE_KEYS = ('internal_waters', 'territorial_sea', 'contiguous_zone',
             'eez', 'high_seas', 'unknown')
WITHIN_24NM_ZONES = ('internal_waters', 'territorial_sea', 'contiguous_zone')

SUB_ZONES = {
    "north": {"name": "東海 / 北方", "lat_min": 26.0, "lat_max": 34.0,
              "lon_min": 122.0, "lon_max": 130.5},
    "east": {"name": "台灣東部", "lat_min": 22.0, "lat_max": 25.5,
             "lon_min": 121.5, "lon_max": 124.0},
    "south": {"name": "南海北部", "lat_min": 15.0, "lat_max": 23.0,
              "lon_min": 110.0, "lon_max": 118.0},
    "west": {"name": "台灣海峽", "lat_min": 23.5, "lat_max": 26.5,
             "lon_min": 118.0, "lon_max": 122.0},
}


# =============================================================================
# 基礎工具
# =============================================================================

def parse_ts(value):
    """ISO 8601 → epoch 秒（UTC）。無效回傳 None。"""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def destination_point(lat, lon, bearing_deg, dist_km):
    """自 (lat, lon) 沿方位角 bearing 前進 dist_km 的目的座標（球面）。"""
    R = 6371.0
    delta = dist_km / R
    theta = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(math.sin(lat1) * math.cos(delta) +
                     math.cos(lat1) * math.sin(delta) * math.cos(theta))
    lon2 = lon1 + math.atan2(math.sin(theta) * math.sin(delta) * math.cos(lat1),
                             math.cos(delta) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180


def gate_km(unc_extra_km):
    """動態匹配半徑：基礎誤差 + 機動不確定性，夾在 [MIN, MAX]。"""
    return max(MIN_GATE_KM, min(MAX_GATE_KM, BASE_ERROR_KM + unc_extra_km))


def _valid_heading(h):
    return isinstance(h, (int, float)) and 0.0 <= h < 360.0


def estimate_position(points, t_epoch):
    """把一艘船的 AIS 軌跡推算到 t_epoch 時刻。

    points: [(t, lat, lon, speed_kn, heading)]，依 t 排序。
    回傳 dict {lat, lon, speed_kn, dt_h, unc_extra_km, method}；
    無法推算（最近點超過 MAX_DT_HOURS）回傳 None。
    method ∈ interpolated / dead_reckoning / hold
    """
    if not points:
        return None
    times = [p[0] for p in points]
    i = bisect_left(times, t_epoch)
    prev = points[i - 1] if i > 0 else None
    nxt = points[i] if i < len(points) else None

    # 兩側都有點且間隔合理 → 時間加權內插
    if prev is not None and nxt is not None:
        gap_h = (nxt[0] - prev[0]) / 3600.0
        if gap_h <= 2 * MAX_DT_HOURS and gap_h > 0:
            w = (t_epoch - prev[0]) / (nxt[0] - prev[0])
            lat = prev[1] + w * (nxt[1] - prev[1])
            lon = prev[2] + w * (nxt[2] - prev[2])
            dt_near_h = min(t_epoch - prev[0], nxt[0] - t_epoch) / 3600.0
            speed = max(prev[3] or 0.0, nxt[3] or 0.0)
            unc = UNC_FRAC_INTERPOLATED * speed * KN_TO_KMH * dt_near_h
            return {'lat': lat, 'lon': lon, 'speed_kn': speed,
                    'dt_h': dt_near_h, 'unc_extra_km': unc,
                    'method': 'interpolated'}

    # 單側 → 取時間上較近的一點做航位推算
    cand = None
    for p in (prev, nxt):
        if p is None:
            continue
        if cand is None or abs(p[0] - t_epoch) < abs(cand[0] - t_epoch):
            cand = p
    if cand is None:
        return None
    dt_s = t_epoch - cand[0]
    dt_h = abs(dt_s) / 3600.0
    if dt_h > MAX_DT_HOURS:
        return None

    speed = cand[3] or 0.0
    heading = cand[4]
    if speed < STATIONARY_SPEED_KN:
        return {'lat': cand[1], 'lon': cand[2], 'speed_kn': speed,
                'dt_h': dt_h, 'unc_extra_km': STATIONARY_UNC_KM,
                'method': 'hold'}
    if not _valid_heading(heading):
        # 在動但不知道往哪 → 位置不動、不確定圈涵蓋整段可能航程
        return {'lat': cand[1], 'lon': cand[2], 'speed_kn': speed,
                'dt_h': dt_h,
                'unc_extra_km': UNC_FRAC_UNKNOWN_HDG * speed * KN_TO_KMH * dt_h,
                'method': 'hold'}

    dist = speed * KN_TO_KMH * dt_h
    bearing = heading if dt_s >= 0 else (heading + 180.0) % 360.0
    lat, lon = destination_point(cand[1], cand[2], bearing, dist)
    unc = UNC_FRAC_DEAD_RECKON * speed * KN_TO_KMH * dt_h
    return {'lat': lat, 'lon': lon, 'speed_kn': speed,
            'dt_h': dt_h, 'unc_extra_km': unc, 'method': 'dead_reckoning'}


# =============================================================================
# 資料載入
# =============================================================================

def load_dark_detections():
    """載入 GFW 標為 unmatched 的偵測點。

    優先讀全量 sar_detections.json；缺檔時退回 dark_vessels.json 的
    dark_details（僅 400 筆樣本、座標 0.01° 捨入 — 標記 degraded）。
    回傳 (records, source_label)。
    """
    data = load_json(SAR_DETECTIONS_PATH, None, expect_type=dict)
    if data and isinstance(data.get('dark_detections'), list):
        recs = [r for r in data['dark_detections']
                if isinstance(r, dict) and r.get('date')
                and r.get('lat') is not None and r.get('lon') is not None]
        return recs, 'sar_detections'

    fallback = load_json(DARK_VESSELS_PATH, None, expect_type=dict)
    if fallback:
        details = ((fallback.get('regions') or {}).get('taiwan_region') or {}) \
            .get('dark_details') or []
        recs = [r for r in details
                if isinstance(r, dict) and r.get('date')
                and r.get('lat') is not None and r.get('lon') is not None]
        if recs:
            print("⚠️ 找不到 sar_detections.json，退回 dark_vessels.json 的 "
                  f"dark_details 樣本（{len(recs)} 筆，精度受限）")
            return recs, 'dark_vessels_fallback'
    return [], 'none'


_VOLTAGE_NAME_RE = re.compile(r'\d+(\.\d+)?V$')
_PCT_TAIL_RE = re.compile(r'\d+%$')


def is_nonvessel_ais(mmsi, name):
    """AtoN / 浮標 / 漁網信標等非船舶 AIS 發射器（與 analyze_suspicious 的
    排除規則一致）。這類目標太小不會是 SAR 偵測到的物體，若拿來當比對
    候選，會把真暗船誤判成已比對而漏掉。"""
    m = str(mmsi or '')
    n = (name or '').upper()
    return (m.startswith('9') or m.startswith('898')
            or '%' in n or 'BUOY' in n
            or bool(_VOLTAGE_NAME_RE.search(n)))


def load_ais_tracks():
    """合併 tier-1 / tier-2 軌跡 + 最新快照 → 每船時間排序的點列。

    回傳 dict: mmsi -> {'name', 'type_name', 'points': [(t, lat, lon,
    speed_kn, heading)]}。非船舶 AIS 發射器（浮標/信標）不納入。
    """
    tracks = {}

    def add_point(mmsi, name, type_name, t, lat, lon, speed, heading):
        if is_nonvessel_ais(mmsi, name):
            return
        tr = tracks.get(mmsi)
        if tr is None:
            tr = {'name': name or '', 'type_name': type_name or 'unknown',
                  'points': []}
            tracks[mmsi] = tr
        else:
            if name:
                tr['name'] = name
            if type_name:
                tr['type_name'] = type_name
        tr['points'].append((t, lat, lon, speed, heading))

    tier_paths = [TIER1_PATH]
    for p in TIER2_PATHS:
        if p.exists():
            tier_paths.append(p)
            break

    for path in tier_paths:
        entries = load_json(path, [], expect_type=list)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            t = parse_ts(entry.get('timestamp'))
            if t is None:
                continue
            for v in entry.get('vessels', []):
                lat, lon = v.get('lat'), v.get('lon')
                mmsi = v.get('mmsi')
                if mmsi is None or lat is None or lon is None:
                    continue
                add_point(str(mmsi), v.get('name'), v.get('type_name'), t,
                          float(lat), float(lon),
                          float(v.get('speed') or 0.0), v.get('heading'))

    snap = load_json(SNAPSHOT_PATH, {}, expect_type=dict)
    for v in snap.get('vessels', []):
        t = parse_ts(v.get('last_update'))
        lat, lon = v.get('lat'), v.get('lon')
        mmsi = v.get('mmsi')
        if t is None or mmsi is None or lat is None or lon is None:
            continue
        add_point(str(mmsi), v.get('name'), v.get('type_name'), t,
                  float(lat), float(lon),
                  float(v.get('speed') or 0.0), v.get('heading'))

    # 排序 + 去除同時刻重複點
    for tr in tracks.values():
        pts = sorted(tr['points'], key=lambda p: p[0])
        deduped = []
        for p in pts:
            if deduped and abs(p[0] - deduped[-1][0]) < 1.0:
                continue
            deduped.append(p)
        tr['points'] = deduped
    return tracks


def load_fixed_infra_mask():
    """可選的靜態固定設施遮罩點（GFW fixed infrastructure 匯出等）。"""
    data = load_json(FIXED_INFRA_PATH, None)
    if data is None:
        return []
    points = data.get('points') if isinstance(data, dict) else data
    mask = []
    for p in points or []:
        if not isinstance(p, dict):
            continue
        lat, lon = p.get('lat'), p.get('lon')
        if lat is None or lon is None:
            continue
        mask.append((float(lat), float(lon),
                     float(p.get('radius_km') or FIXED_INFRA_MASK_KM)))
    return mask


# =============================================================================
# 固定設施過濾
# =============================================================================

def infra_cell(lat, lon):
    return (round(lat / INFRA_CELL_DEG), round(lon / INFRA_CELL_DEG))


def detect_infrastructure_cells(dark_records):
    """重現性啟發式：同一 0.01° cell 在 ≥ INFRA_MIN_DATES 個不同日期
    都有暗偵測 → 固定設施（風機/平台/養殖）。回傳 {cell: sorted dates}。"""
    cell_dates = defaultdict(set)
    for r in dark_records:
        cell_dates[infra_cell(r['lat'], r['lon'])].add(r['date'])
    return {c: sorted(ds) for c, ds in cell_dates.items()
            if len(ds) >= INFRA_MIN_DATES}


def build_infra_filter(dark_records, static_mask):
    """回傳 (is_infra(rec) -> bool, infra_cells dict)。"""
    infra_cells = detect_infrastructure_cells(dark_records)

    # 靜態遮罩粗網格索引（半徑通常 ~1km，0.1° 桶即可）
    mask_grid = defaultdict(list)
    for lat, lon, radius in static_mask:
        mask_grid[(round(lat, 1), round(lon, 1))].append((lat, lon, radius))

    def is_infra(rec):
        if infra_cell(rec['lat'], rec['lon']) in infra_cells:
            return True
        if mask_grid:
            base_lat, base_lon = round(rec['lat'], 1), round(rec['lon'], 1)
            for dlat in (-0.1, 0.0, 0.1):
                for dlon in (-0.1, 0.0, 0.1):
                    key = (round(base_lat + dlat, 1), round(base_lon + dlon, 1))
                    for lat, lon, radius in mask_grid.get(key, ()):
                        if haversine_km(rec['lat'], rec['lon'], lat, lon) <= radius:
                            return True
        return False

    return is_infra, infra_cells


# =============================================================================
# 過境時刻與指派
# =============================================================================

def pass_candidates(rec):
    """一筆偵測的候選成像時刻 [(pass_label, t_epoch)]。

    記錄帶 timestamp 時直接使用；否則用該日期的 Sentinel-1 兩個過境窗。
    """
    ts = rec.get('timestamp')
    if ts:
        t = parse_ts(ts)
        if t is not None:
            return [('exact', t)]
    out = []
    for label, hh, mm in S1_PASS_TIMES_UTC:
        t = parse_ts(f"{rec['date']}T{hh:02d}:{mm:02d}:00+00:00")
        if t is not None:
            out.append((label, t))
    return out


def _cand_cell(lat, lon):
    return (int(math.floor(lat / CANDIDATE_CELL_DEG)),
            int(math.floor(lon / CANDIDATE_CELL_DEG)))


def estimates_for_time(tracks, t_epoch):
    """全船隊推算到 t_epoch，回傳 (est: mmsi->estimate, grid 空間索引)。"""
    est = {}
    grid = defaultdict(list)
    for mmsi, tr in tracks.items():
        e = estimate_position(tr['points'], t_epoch)
        if e is None:
            continue
        est[mmsi] = e
        grid[_cand_cell(e['lat'], e['lon'])].append(mmsi)
    return est, grid


def candidates_for_detection(rec, est, grid):
    """gating：回傳 [(cost, mmsi, dist_km, gate)]，cost = dist/gate ≤ 1。"""
    out = []
    ci, cj = _cand_cell(rec['lat'], rec['lon'])
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for mmsi in grid.get((ci + di, cj + dj), ()):
                e = est[mmsi]
                dist = haversine_km(rec['lat'], rec['lon'], e['lat'], e['lon'])
                g = gate_km(e['unc_extra_km'])
                if dist <= g:
                    out.append((dist / g, mmsi, dist, g))
    return out


def solve_assignment(candidate_lists):
    """每個過境群組內的偵測↔船一對一指派。

    candidate_lists: {det_idx: [(cost, mmsi, dist, gate)]}
    回傳 {det_idx: (cost, mmsi, dist, gate)}。
    有 scipy 用匈牙利演算法（全域最小成本）；否則用全域成本排序貪婪
    最近鄰（gating 後依 cost 升冪，一船一偵測）。
    """
    try:
        return _solve_hungarian(candidate_lists)
    except ImportError:
        return _solve_greedy(candidate_lists)


def _solve_greedy(candidate_lists):
    pairs = []
    for det_idx, cands in candidate_lists.items():
        for cost, mmsi, dist, g in cands:
            pairs.append((cost, det_idx, mmsi, dist, g))
    pairs.sort(key=lambda p: p[0])
    used_det, used_ves = set(), set()
    result = {}
    for cost, det_idx, mmsi, dist, g in pairs:
        if det_idx in used_det or mmsi in used_ves:
            continue
        used_det.add(det_idx)
        used_ves.add(mmsi)
        result[det_idx] = (cost, mmsi, dist, g)
    return result


def _solve_hungarian(candidate_lists):
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    det_ids = [d for d, c in candidate_lists.items() if c]
    if not det_ids:
        return {}
    vessels = sorted({mmsi for d in det_ids
                      for _, mmsi, _, _ in candidate_lists[d]})
    v_index = {m: j for j, m in enumerate(vessels)}
    BIG = 1e6
    cost = np.full((len(det_ids), len(vessels)), BIG)
    info = {}
    for i, d in enumerate(det_ids):
        for c, mmsi, dist, g in candidate_lists[d]:
            j = v_index[mmsi]
            if c < cost[i, j]:
                cost[i, j] = c
                info[(i, j)] = (c, mmsi, dist, g)
    rows, cols = linear_sum_assignment(cost)
    result = {}
    for i, j in zip(rows, cols):
        if cost[i, j] >= BIG:
            continue
        result[det_ids[i]] = info[(i, j)]
    return result


# =============================================================================
# 船長交叉驗證
# =============================================================================

def get_ais_length_m(profile):
    """從船舶 profile 取登記船長（公尺）。目前資料源不帶船長時回傳 None，
    欄位到位（dim_a/dim_b 或 length_m）後自動生效。"""
    if not isinstance(profile, dict):
        return None
    for key in ('length_m', 'length', 'last_length'):
        v = profile.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    dim_a, dim_b = profile.get('dim_a'), profile.get('dim_b')
    if isinstance(dim_a, (int, float)) and isinstance(dim_b, (int, float)) \
            and dim_a + dim_b > 0:
        return float(dim_a + dim_b)
    return None


def check_length_mismatch(sar_length_m, ais_length_m):
    """SAR 估計船長 vs AIS 登記船長差異過大 → AIS 身分冒用線索。"""
    if not sar_length_m or not ais_length_m:
        return None
    diff = abs(sar_length_m - ais_length_m)
    frac = diff / max(sar_length_m, ais_length_m)
    return frac > LENGTH_MISMATCH_FRAC and diff > LENGTH_MISMATCH_MIN_M


# =============================================================================
# 輸出層
# =============================================================================

_zone_cache = {}


def classify_zone_cached(lat, lon):
    key = (round(lat, 2), round(lon, 2))
    z = _zone_cache.get(key)
    if z is None:
        z = geofence.classify_maritime_zone(lat, lon)['zone']
        _zone_cache[key] = z
    return z


def subzone_of(lat, lon):
    for zone_id, z in SUB_ZONES.items():
        if z['lat_min'] <= lat <= z['lat_max'] and \
                z['lon_min'] <= lon <= z['lon_max']:
            return zone_id
    return None


def build_density_grid(records):
    """殘餘暗船 0.1° 密度網格（熱圖用）：[{lat, lon, count}]。"""
    counts = defaultdict(int)
    for r in records:
        cell = (round(r['lat'] / DENSITY_CELL_DEG) * DENSITY_CELL_DEG,
                round(r['lon'] / DENSITY_CELL_DEG) * DENSITY_CELL_DEG)
        counts[cell] += 1
    return [{'lat': round(lat, 1), 'lon': round(lon, 1), 'count': c}
            for (lat, lon), c in sorted(counts.items(),
                                        key=lambda kv: -kv[1])]


def build_zone_series(all_dark, screened):
    """按法域與方位子區域的每日時間序列（raw + screened）。

    raw = 全部 GFW 暗偵測；screened = 過濾固定設施 + 本地重比對後的殘餘。
    可直接餵 changepoint detection。
    """
    dates = sorted({r['date'] for r in all_dark})
    d_index = {d: i for i, d in enumerate(dates)}
    n = len(dates)

    raw_total = [0] * n
    screened_total = [0] * n
    by_zone = {z: [0] * n for z in ZONE_KEYS}
    within_24nm = [0] * n
    raw_within_24nm = [0] * n
    by_subzone = {z: [0] * n for z in SUB_ZONES}

    for r in all_dark:
        i = d_index[r['date']]
        raw_total[i] += 1
        if classify_zone_cached(r['lat'], r['lon']) in WITHIN_24NM_ZONES:
            raw_within_24nm[i] += 1
    for r in screened:
        i = d_index[r['date']]
        screened_total[i] += 1
        zone = classify_zone_cached(r['lat'], r['lon'])
        by_zone[zone][i] += 1
        if zone in WITHIN_24NM_ZONES:
            within_24nm[i] += 1
        sz = subzone_of(r['lat'], r['lon'])
        if sz:
            by_subzone[sz][i] += 1

    return {
        'dates': dates,
        'raw_total': raw_total,
        'screened_total': screened_total,
        'raw_within_24nm': raw_within_24nm,
        'screened_within_24nm': within_24nm,
        'screened_by_zone': by_zone,
        'screened_by_subzone': by_subzone,
    }


# =============================================================================
# 主流程
# =============================================================================

def run_matching(dark_records, tracks, static_mask=(), profiles=None):
    """核心比對流程（純函式，便於測試）。回傳輸出 dict（不含 updated_at）。"""
    profiles = profiles or {}

    # 1. 固定設施過濾
    is_infra, infra_cells = build_infra_filter(dark_records, list(static_mask))
    infra_recs, work_recs = [], []
    for r in dark_records:
        (infra_recs if is_infra(r) else work_recs).append(r)

    # AIS 覆蓋範圍：早於本地軌跡起點的偵測無從驗證
    all_times = [p[0] for tr in tracks.values() for p in (tr['points'][:1] or ())]
    coverage_start = min(all_times) if all_times else None
    all_end = [tr['points'][-1][0] for tr in tracks.values() if tr['points']]
    coverage_end = max(all_end) if all_end else None

    # 2-4. 逐過境群組：內插 + 動態 gate + 一對一指派
    #    每筆偵測在其日期的每個過境窗各嘗試一次；同筆偵測兩窗都中時取
    #    cost 較低者（一船在同一過境窗內只能配一筆）。
    groups = defaultdict(list)   # (date, pass_label, t_epoch) -> [det_idx]
    in_coverage = set()
    for idx, r in enumerate(work_recs):
        for label, t in pass_candidates(r):
            if coverage_start is not None and t < coverage_start - 3600 * MAX_DT_HOURS:
                continue
            in_coverage.add(idx)
            groups[(r['date'], label, t)].append(idx)

    est_cache = {}
    proposals = []   # (cost, det_idx, mmsi, dist, gate, pass_label, t_epoch)
    for (date, label, t), det_ids in sorted(groups.items()):
        if t not in est_cache:
            est_cache = {t: estimates_for_time(tracks, t)}  # 只留當前時刻
        est, grid = est_cache[t]
        cand_lists = {i: candidates_for_detection(work_recs[i], est, grid)
                      for i in det_ids}
        assigned = solve_assignment(cand_lists)
        for det_idx, (cost, mmsi, dist, g) in assigned.items():
            proposals.append((cost, det_idx, mmsi, dist, g, label, t))

    # 合併兩個過境窗的提案：每筆偵測取最佳；同一 (船, 過境) 不得重複
    proposals.sort(key=lambda p: p[0])
    matched = {}          # det_idx -> proposal
    used_vessel_pass = set()
    for cost, det_idx, mmsi, dist, g, label, t in proposals:
        if det_idx in matched or (mmsi, t) in used_vessel_pass:
            continue
        matched[det_idx] = (cost, mmsi, dist, g, label, t)
        used_vessel_pass.add((mmsi, t))

    # 5. 船長交叉驗證（偵測帶 SAR 船長且 AIS 側查得到船長時）
    length_checks = []
    for det_idx, (cost, mmsi, dist, g, label, t) in matched.items():
        sar_len = work_recs[det_idx].get('length_m')
        ais_len = get_ais_length_m(profiles.get(mmsi))
        verdict = check_length_mismatch(sar_len, ais_len)
        if verdict is None:
            continue
        length_checks.append({
            'mmsi': mmsi,
            'date': work_recs[det_idx]['date'],
            'sar_length_m': round(float(sar_len), 1),
            'ais_length_m': round(float(ais_len), 1),
            'mismatch': verdict,
        })

    # 分類結果集
    rematched, residual = [], []
    no_coverage = 0
    for idx, r in enumerate(work_recs):
        if idx in matched:
            cost, mmsi, dist, g, label, t = matched[idx]
            tr = tracks.get(mmsi, {})
            e = estimate_position(tr.get('points', []), t) or {}
            rematched.append({
                'lat': r['lat'], 'lon': r['lon'], 'date': r['date'],
                'pass': label,
                'mmsi': mmsi,
                'name': tr.get('name', ''),
                'type_name': tr.get('type_name', 'unknown'),
                'distance_km': round(dist, 2),
                'gate_km': round(g, 2),
                'dt_minutes': round(e.get('dt_h', 0.0) * 60),
                'method': e.get('method', ''),
            })
        else:
            entry = dict(r)
            entry['zone'] = classify_zone_cached(r['lat'], r['lon'])
            entry['in_ais_coverage'] = idx in in_coverage
            if idx not in in_coverage:
                no_coverage += 1
            residual.append(entry)

    zone_series = build_zone_series(dark_records, residual)
    density = build_density_grid(residual)

    dark_total = len(dark_records)
    summary = {
        'dark_total': dark_total,
        'infrastructure_filtered': len(infra_recs),
        'in_ais_coverage': len(in_coverage),
        'no_ais_coverage': no_coverage,
        'rematched_local': len(rematched),
        'residual_dark': len(residual),
        'rematch_rate_of_coverage_pct': (
            round(len(rematched) / len(in_coverage) * 100, 1)
            if in_coverage else 0.0),
        'false_dark_removed_pct': (
            round((len(infra_recs) + len(rematched)) / dark_total * 100, 1)
            if dark_total else 0.0),
        'ais_vessels_indexed': len(tracks),
        'ais_coverage': {
            'start': (datetime.fromtimestamp(coverage_start, tz=timezone.utc)
                      .isoformat().replace('+00:00', 'Z')
                      if coverage_start else None),
            'end': (datetime.fromtimestamp(coverage_end, tz=timezone.utc)
                    .isoformat().replace('+00:00', 'Z')
                    if coverage_end else None),
        },
        'length_checked': len(length_checks),
        'length_mismatches': sum(1 for c in length_checks if c['mismatch']),
    }

    infra_details = [
        {'lat': round(ci * INFRA_CELL_DEG, 3),
         'lon': round(cj * INFRA_CELL_DEG, 3),
         'dates_seen': len(dates)}
        for (ci, cj), dates in sorted(infra_cells.items(),
                                      key=lambda kv: -len(kv[1]))
    ]

    return {
        'params': {
            'base_error_km': BASE_ERROR_KM,
            'min_gate_km': MIN_GATE_KM,
            'max_gate_km': MAX_GATE_KM,
            'max_dt_hours': MAX_DT_HOURS,
            'infra_min_dates': INFRA_MIN_DATES,
            's1_pass_times_utc': [f"{h:02d}:{m:02d}" for _, h, m in
                                  S1_PASS_TIMES_UTC],
        },
        'summary': summary,
        'rematched': rematched[:MAX_REMATCH_DETAILS],
        'infrastructure_cells': infra_details[:MAX_INFRA_DETAILS],
        'residual_dark': residual[:MAX_RESIDUAL_DETAILS],
        'density_grid': density,
        'zone_series': zone_series,
        'length_checks': length_checks,
    }


def main():
    print("=" * 70)
    print("🛰️  SAR × 本地 AIS 重比對 — GFW 暗船去偽")
    print(f"執行時間: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    print("=" * 70)

    dark_records, source = load_dark_detections()
    if not dark_records:
        print("⚠️ 沒有可用的暗船偵測資料（先跑 fetch_gfw_data.py），跳過")
        return 0
    print(f"🔦 暗船偵測點: {len(dark_records)} 筆（來源: {source}）")

    tracks = load_ais_tracks()
    total_points = sum(len(tr['points']) for tr in tracks.values())
    print(f"🚢 本地 AIS: {len(tracks)} 艘、{total_points} 個軌跡點")
    if not tracks:
        print("⚠️ 沒有本地 AIS 軌跡，只做固定設施過濾")

    static_mask = load_fixed_infra_mask()
    if static_mask:
        print(f"🏗️ 靜態固定設施遮罩: {len(static_mask)} 點")

    result = run_matching(dark_records, tracks, static_mask)
    result['updated_at'] = datetime.now(timezone.utc).isoformat() \
        .replace('+00:00', 'Z')
    result['source'] = source

    atomic_write_json(OUTPUT_PATH, result, compact=True)

    s = result['summary']
    print("\n" + "=" * 70)
    print(f"✅ 已儲存: {OUTPUT_PATH}")
    print(f"   暗偵測總數:      {s['dark_total']}")
    print(f"   固定設施過濾:    {s['infrastructure_filtered']}")
    print(f"   AIS 覆蓋內:      {s['in_ais_coverage']}")
    print(f"   本地重比對成功:  {s['rematched_local']} "
          f"({s['rematch_rate_of_coverage_pct']}% of coverage)")
    print(f"   殘餘暗船:        {s['residual_dark']}")
    print(f"   假暗船剔除率:    {s['false_dark_removed_pct']}%")
    if s['length_checked']:
        print(f"   船長交叉驗證:    {s['length_checked']} 筆 / "
              f"{s['length_mismatches']} 不符")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
