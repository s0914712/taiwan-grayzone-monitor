#!/usr/bin/env python3
"""
================================================================================
海底電纜威脅偵測 — 可疑船隻分析引擎
Suspicious Vessel Analysis: Submarine Cable Threat Detection
================================================================================

偵測邏輯（針對海纜破壞威脅 + AIS 偽訊號）：
  1. 海底電纜鄰近活動 (Cable Proximity)
     - 船隻航跡經過海纜路線 5 公里內
  2. Z 字型移動模式 (Zigzag Pattern)
     - 頻繁改變航向，疑似拖錨或破壞行為
  3. 200 公尺等深線活動 (Continental Shelf Edge)
     - 在大陸棚邊緣活動，海纜密集區
  4. AIS 身分變更 (Identity Manipulation)
     - 變更船名、呼號、IMO 等識別資訊
  5. AIS 偽訊號偵測 (Spoofing Detection)
     a. 不可能物理 — 瞬移、速度/航向不一致
     b. 方形軌跡 — 多次 ~90° 轉彎 + 封閉路徑
     c. 圓形軌跡 — 半徑 CV 極低 + 弧度覆蓋 > 270°
================================================================================
"""

import json
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path("data")
HISTORY_FILE = DATA_DIR / "vessel_profiles.json"
TRACK_HISTORY_FILE = DATA_DIR / "ais_track_history.json"
TRACK_COMMERCIAL_FILE = DATA_DIR / "ais_track_commercial.json"
CABLE_GEO_FILE = DATA_DIR / "cable-geo.json"
IDENTITY_EVENTS_FILE = DATA_DIR / "identity_events.json"
SANCTIONS_FILE = DATA_DIR / "un_sanctions_vessels.json"
OUTPUT_FILE = DATA_DIR / "suspicious_vessels.json"
ITU_MARS_CACHE = DATA_DIR / "itu_mars_cache.json"
SHIP_TRANSFERS_FILE = DATA_DIR / "ship_transfers.json"

# ── 門檻設定 ────────────────────────────────────────────
CABLE_PROXIMITY_KM = 5.0          # 海纜 5 公里內視為鄰近
CABLE_LOITER_HOURS = 3.0          # 海纜鄰近低速徘徊 > 3 小時
CABLE_LOITER_MAX_KNOTS = 8.0      # 低速定義 < 8 knots
ZIGZAG_HEADING_CHANGE_DEG = 45    # 航向變化 > 45° 視為一次轉向
ZIGZAG_MIN_TURNS = 3              # 至少 3 次轉向才算 Z 字型
DEPTH_200M_CONTOUR_KM = 10.0      # 200m 等深線緩衝區寬度
NAME_CHANGE_THRESHOLD = 2         # 船名變更 ≥ 2 次
GOING_DARK_GAP_HOURS = 18         # AIS 消失 > 18 小時

# ── AIS 偽訊號偵測門檻 (Spoofing Detection) ───────────────
SPOOF_TELEPORT_KMH = 100.0         # 最大合理速度 ~54kn；超過即瞬移
SPOOF_SPEED_MISMATCH_RATIO = 3.0   # 計算速度 / 回報速度 比值門檻
SPOOF_BEARING_MISMATCH_DEG = 60.0  # 計算航向 vs 回報 COG 差異門檻
SPOOF_BOX_ANGLE_TOLERANCE = 25.0   # 90° ± 25° 視為直角轉彎（65°-115°）
SPOOF_BOX_MIN_TURNS = 3            # 至少 3 次直角轉彎
SPOOF_BOX_CLOSURE_KM = 5.0         # 起終點 < 5km 視為封閉路徑
SPOOF_CIRCLE_MIN_POINTS = 6        # 圓形偵測最少點數
SPOOF_CIRCLE_RADIUS_CV = 0.25      # 半徑變異係數門檻 (std/mean < 0.25)
SPOOF_CIRCLE_MAX_RADIUS_KM = 5.0   # 半徑 > 5km 排除（可能正常航行）
SPOOF_CIRCLE_MIN_RADIUS_KM = 0.1   # 半徑 < 0.1km 排除（GPS 漂移）
SPOOF_CIRCLE_MIN_ARC_DEG = 270.0   # 弧度覆蓋 > 270° 才算圓形

# ── 船型威脅乘數 ──────────────────────────────────────────
# 商船（cargo/tanker/lng）錨鍊長、噸位大，對海纜威脅高 → ×1.0
# 漁船常態作業、體積小、危險性低 → ×0.2
# 其他/不明 → ×0.5
VESSEL_TYPE_MULTIPLIER = {
    'cargo': 1.0,
    'tanker': 1.0,
    'lng': 1.0,
    'fishing': 0.2,
    'other': 0.5,
    'unknown': 0.5,
}

# ── STS 旁靠加分 ──────────────────────────────────────────
STS_SUSPICIOUS_SCORE = 5   # 涉及可疑旁靠事件
STS_ANY_SCORE = 2          # 涉及任何旁靠事件

# ── 前十大船旗國 MMSI MID（國籍非前十大視為額外可疑）────────
# MID = MMSI 前 3 碼，對照 ITU MID 表
TOP_10_FLAG_MIDS = {
    # Panama
    '351', '352', '353', '354', '355', '356', '357',
    # Liberia
    '636', '637',
    # Marshall Islands
    '538',
    # Hong Kong
    '477',
    # Singapore
    '563', '564', '565', '566',
    # Bahamas
    '308', '309',
    # Malta
    '215', '229', '249', '256',
    # China
    '412', '413', '414',
    # Japan
    '431', '432',
    # Taiwan (ROC)
    '416',
}

# ── 排除規則 (Exclusion Rules) ──────────────────────────────
# 符合任一規則的船隻/設備將被排除在可疑計算之外。
# 新增規則只需在此列表加入一個 dict：
#   id:    唯一識別碼（用於輸出 JSON 標記）
#   label: 人類可讀的排除原因（中文）
#   check: function(mmsi: str, names: list[str]) -> bool
#
# names 參數為該 MMSI 歷史上使用過的所有船名列表。
# ───────────────────────────────────────────────────────────
EXCLUSION_RULES = [
    {
        'id': 'mmsi_9xx',
        'label': '潛水浮標/AtoN (MMSI 9開頭)',
        'check': lambda mmsi, names: mmsi.startswith('9'),
    },
    {
        'id': 'mmsi_898',
        'label': '漁網標記 (MMSI 898開頭)',
        'check': lambda mmsi, names: mmsi.startswith('898'),
    },
    {
        'id': 'name_percent',
        'label': '漁網/魚標信標 (名稱含%)',
        'check': lambda mmsi, names: any('%' in n for n in names if n),
    },
    {
        'id': 'name_buoy',
        'label': '浮標 (名稱含BUOY)',
        'check': lambda mmsi, names: any(
            'BUOY' in (n or '').upper() for n in names if n
        ),
    },
    {
        'id': 'name_voltage_suffix',
        'label': '漁網信標 (名稱尾部電壓值 V)',
        'check': lambda mmsi, names: any(
            re.search(r'\d+\.?\d*V$', (n or '').strip().upper())
            for n in names if n
        ),
    },
    {
        'id': 'name_digit_percent_suffix',
        'label': '漁網信標 (名稱尾部 數字%)',
        'check': lambda mmsi, names: any(
            re.search(r'\d+%$', (n or '').strip())
            for n in names if n
        ),
    },
]


def check_exclusion_rules(mmsi, names):
    """
    檢查 MMSI / 船名是否符合任一排除規則。
    回傳: (excluded: bool, matched_rules: list[dict])
    每個 matched_rule = {'id': ..., 'label': ...}
    """
    matched = []
    for rule in EXCLUSION_RULES:
        try:
            if rule['check'](mmsi, names):
                matched.append({'id': rule['id'], 'label': rule['label']})
        except Exception:
            continue
    return len(matched) > 0, matched


# ── 台灣周邊 200m 等深線近似座標 ─────────────────────────
# 大陸棚邊緣（西側較淺、東側急降）
DEPTH_200M_CONTOUR = [
    # 台灣東部深水區邊緣（太平洋側）
    (25.5, 122.2), (25.0, 122.1), (24.5, 121.8),
    (24.0, 121.5), (23.5, 121.3), (23.0, 121.0),
    (22.5, 120.8), (22.0, 120.6), (21.5, 120.5),
    # 巴士海峽
    (21.0, 120.8), (20.5, 121.0),
    # 南海北部
    (21.0, 119.0), (21.5, 118.0), (22.0, 117.0),
    # 台灣海峽西側大陸棚邊緣
    (23.0, 118.0), (24.0, 118.5), (25.0, 119.5),
    (25.5, 120.5), (26.0, 121.0),
]

# ── 台灣周邊海纜座標快取 ─────────────────────────────────
_cable_segments = None


def load_cable_segments():
    """載入海纜 GeoJSON，提取台灣周邊的線段座標"""
    global _cable_segments
    if _cable_segments is not None:
        return _cable_segments

    _cable_segments = []

    if not CABLE_GEO_FILE.exists():
        print("⚠️ cable-geo.json not found, skipping cable proximity analysis")
        return _cable_segments

    with open(CABLE_GEO_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for feat in data.get('features', []):
        slug = feat.get('properties', {}).get('slug', '')
        geom = feat.get('geometry', {})
        coords = geom.get('coordinates', [])

        for segment in coords:
            tw_points = []
            for lon, lat in segment:
                # 只保留台灣周邊 (lat 19-28, lon 115-130)
                if 19 <= lat <= 28 and 115 <= lon <= 130:
                    tw_points.append((lat, lon))

            if len(tw_points) >= 2:
                lats = [p[0] for p in tw_points]
                lons = [p[1] for p in tw_points]
                _cable_segments.append({
                    'slug': slug,
                    'points': tw_points,
                    'bbox': (min(lats), min(lons), max(lats), max(lons)),
                })

    print(f"📡 載入 {len(_cable_segments)} 條台灣周邊海纜線段")
    return _cable_segments


def haversine_km(lat1, lon1, lat2, lon2):
    """兩點間距離（公里）"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def point_to_segment_distance_km(plat, plon, lat1, lon1, lat2, lon2):
    """點到線段的最短距離（公里），用投影法"""
    dx = lat2 - lat1
    dy = lon2 - lon1
    if dx == 0 and dy == 0:
        return haversine_km(plat, plon, lat1, lon1)

    t = max(0, min(1, ((plat - lat1) * dx + (plon - lon1) * dy) / (dx*dx + dy*dy)))
    proj_lat = lat1 + t * dx
    proj_lon = lon1 + t * dy
    return haversine_km(plat, plon, proj_lat, proj_lon)


def calc_bearing(lat1, lon1, lat2, lon2):
    """計算兩點間方位角 (0-360°)"""
    dlon = math.radians(lon2 - lon1)
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - \
        math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def angular_diff(a, b):
    """兩角度之間的最小差值 (0-180°)"""
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


# =========================================================================
# AIS 偽訊號偵測 (Spoofing Detection)
# =========================================================================

def check_impossible_physics(track_points):
    """
    偵測不可能物理現象：
    - 瞬移 (teleportation): 計算速度超過 SPOOF_TELEPORT_KMH
    - 速度不一致: 計算速度 vs 回報 SOG 比值 > SPOOF_SPEED_MISMATCH_RATIO
    - 航向不一致: 計算航向 vs 回報 COG 差異 > SPOOF_BEARING_MISMATCH_DEG
    回傳: (is_suspicious, details)
    """
    if len(track_points) < 2:
        return False, {}

    teleport_count = 0
    max_calc_speed = 0
    speed_mismatch_count = 0
    bearing_mismatch_count = 0
    pairs_checked = 0

    for i in range(1, len(track_points)):
        p1 = track_points[i - 1]
        p2 = track_points[i]

        lat1, lon1 = p1.get('lat'), p1.get('lon')
        lat2, lon2 = p2.get('lat'), p2.get('lon')
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            continue

        # 計算時間差
        try:
            t1 = datetime.fromisoformat(p1['t'].replace('Z', '+00:00'))
            t2 = datetime.fromisoformat(p2['t'].replace('Z', '+00:00'))
            dt_hours = (t2 - t1).total_seconds() / 3600
        except (ValueError, KeyError):
            continue

        if dt_hours <= 0:
            continue

        # 跳過 going-dark 間隔（已由其他偵測器處理）
        if dt_hours > GOING_DARK_GAP_HOURS:
            continue

        pairs_checked += 1
        dist_km = haversine_km(lat1, lon1, lat2, lon2)
        calc_speed_kmh = dist_km / dt_hours

        if calc_speed_kmh > max_calc_speed:
            max_calc_speed = calc_speed_kmh

        # 瞬移偵測
        if calc_speed_kmh > SPOOF_TELEPORT_KMH:
            teleport_count += 1

        # 速度不一致偵測
        reported_sog = p1.get('speed', 0)
        reported_kmh = reported_sog * 1.852  # knots → km/h
        if calc_speed_kmh > 5 and reported_kmh > 5:  # 避免低速噪音
            ratio = calc_speed_kmh / reported_kmh
            if ratio > SPOOF_SPEED_MISMATCH_RATIO or \
               ratio < (1.0 / SPOOF_SPEED_MISMATCH_RATIO):
                speed_mismatch_count += 1

        # 航向不一致偵測
        if dist_km >= 1.0:  # 移動距離夠大才比較航向
            calc_brg = calc_bearing(lat1, lon1, lat2, lon2)
            reported_hdg = p1.get('heading')
            if reported_hdg is not None and reported_hdg > 0:
                diff = angular_diff(calc_brg, reported_hdg)
                if diff > SPOOF_BEARING_MISMATCH_DEG:
                    bearing_mismatch_count += 1

    is_suspicious = (teleport_count > 0 or
                     speed_mismatch_count >= 2 or
                     bearing_mismatch_count >= 2)

    return is_suspicious, {
        'teleport_count': teleport_count,
        'max_calc_speed_kmh': round(max_calc_speed, 1),
        'speed_mismatch_count': speed_mismatch_count,
        'bearing_mismatch_count': bearing_mismatch_count,
        'pairs_checked': pairs_checked,
    }


def check_box_pattern(track_points):
    """
    偵測方形軌跡圖案 (Box Pattern)：
    - 多次接近 90° 的轉彎 (65°-115°)
    - 路徑封閉或包圍面積極小
    常見於 AIS 位置偽造（GPS spoofing）。
    回傳: (is_box, details)
    """
    # 過濾有效移動點
    moving = [p for p in track_points
              if p.get('lat') is not None and p.get('lon') is not None
              and p.get('speed', 0) > 0.5]

    if len(moving) < 4:
        return False, {}

    # 計算連續航向
    bearings = []
    for i in range(1, len(moving)):
        dist = haversine_km(moving[i-1]['lat'], moving[i-1]['lon'],
                            moving[i]['lat'], moving[i]['lon'])
        if dist < 0.1:  # 跳過幾乎不動的點
            continue
        brg = calc_bearing(moving[i-1]['lat'], moving[i-1]['lon'],
                           moving[i]['lat'], moving[i]['lon'])
        bearings.append((brg, i))

    if len(bearings) < 4:
        return False, {}

    # 計算航向變化，統計接近 90° 的轉彎
    right_angle_turns = 0
    for j in range(1, len(bearings)):
        delta = angular_diff(bearings[j][0], bearings[j-1][0])
        if abs(delta - 90) <= SPOOF_BOX_ANGLE_TOLERANCE:
            right_angle_turns += 1

    # 檢查路徑封閉性
    first_pt = moving[0]
    last_pt = moving[-1]
    closure_km = haversine_km(first_pt['lat'], first_pt['lon'],
                              last_pt['lat'], last_pt['lon'])
    path_closed = closure_km < SPOOF_BOX_CLOSURE_KM

    # 計算 bounding box 對角線
    lats = [p['lat'] for p in moving]
    lons = [p['lon'] for p in moving]
    bbox_km = haversine_km(min(lats), min(lons), max(lats), max(lons))

    is_box = (right_angle_turns >= SPOOF_BOX_MIN_TURNS and
              (path_closed or bbox_km < 5.0))

    return is_box, {
        'right_angle_turns': right_angle_turns,
        'path_closed': path_closed,
        'closure_distance_km': round(closure_km, 2),
        'bounding_box_km': round(bbox_km, 2),
    }


def check_circle_pattern(track_points):
    """
    偵測圓形軌跡圖案 (Circle Pattern)：
    - 計算所有點到質心的距離（半徑）
    - 半徑變異係數 (CV) 極低 = 高度對稱
    - 弧度覆蓋 > 270° = 接近完整圓
    常見於 AIS 訊號蓄意操控偽跡。
    回傳: (is_circle, details)
    """
    # 過濾有效移動點
    moving = [p for p in track_points
              if p.get('lat') is not None and p.get('lon') is not None
              and p.get('speed', 0) > 0.5]

    if len(moving) < SPOOF_CIRCLE_MIN_POINTS:
        return False, {}

    # 計算質心
    clat = sum(p['lat'] for p in moving) / len(moving)
    clon = sum(p['lon'] for p in moving) / len(moving)

    # 計算每點到質心的半徑
    radii = [haversine_km(clat, clon, p['lat'], p['lon']) for p in moving]
    mean_r = sum(radii) / len(radii)

    if mean_r < SPOOF_CIRCLE_MIN_RADIUS_KM or mean_r > SPOOF_CIRCLE_MAX_RADIUS_KM:
        return False, {}

    # 變異係數
    variance = sum((r - mean_r) ** 2 for r in radii) / len(radii)
    std_r = math.sqrt(variance)
    cv = std_r / mean_r if mean_r > 0 else 999

    # 計算弧度覆蓋：各點相對於質心的方位角
    angles = sorted(calc_bearing(clat, clon, p['lat'], p['lon'])
                    for p in moving)

    # 計算最大角度間隙 → 覆蓋 = 360 - 最大間隙
    if len(angles) < 2:
        arc_coverage = 0
    else:
        gaps = [angles[i+1] - angles[i] for i in range(len(angles) - 1)]
        gaps.append(360 - angles[-1] + angles[0])  # wrap-around gap
        arc_coverage = 360 - max(gaps)

    is_circle = (cv < SPOOF_CIRCLE_RADIUS_CV and
                 arc_coverage >= SPOOF_CIRCLE_MIN_ARC_DEG)

    return is_circle, {
        'center_lat': round(clat, 4),
        'center_lon': round(clon, 4),
        'mean_radius_km': round(mean_r, 3),
        'radius_cv': round(cv, 3),
        'arc_coverage_deg': round(arc_coverage, 1),
        'point_count': len(moving),
    }


def check_cable_proximity(track_points):
    """
    檢查船隻航跡是否經過海纜附近
    同時偵測低速徘徊（<8kn 在海纜 5km 內超過 3 小時）
    回傳: (is_near, details)
    """
    cables = load_cable_segments()
    if not cables:
        return False, {}

    # 計算船隻航跡的 bounding box，用於快速排除不相關的海纜
    valid_pts = [p for p in track_points
                 if p.get('lat') is not None and p.get('lon') is not None]
    if not valid_pts:
        return False, {}
    # CABLE_PROXIMITY_KM ≈ 0.045° buffer at equator, use 0.06° for safety
    bbox_buf = 0.06
    tk_lat_min = min(p['lat'] for p in valid_pts) - bbox_buf
    tk_lat_max = max(p['lat'] for p in valid_pts) + bbox_buf
    tk_lon_min = min(p['lon'] for p in valid_pts) - bbox_buf
    tk_lon_max = max(p['lon'] for p in valid_pts) + bbox_buf

    # 預篩選：只保留 bbox 與航跡重疊的海纜
    nearby_cables = [c for c in cables
                     if c['bbox'][0] <= tk_lat_max and c['bbox'][2] >= tk_lat_min
                     and c['bbox'][1] <= tk_lon_max and c['bbox'][3] >= tk_lon_min]

    near_cables = set()
    min_dist = float('inf')
    near_count = 0
    loiter_slow_timestamps = []  # 海纜鄰近且低速的時間戳

    for pt in valid_pts:
        plat = pt['lat']
        plon = pt['lon']

        is_near_cable = False
        for cable in nearby_cables:
            points = cable['points']
            for i in range(len(points) - 1):
                dist = point_to_segment_distance_km(
                    plat, plon,
                    points[i][0], points[i][1],
                    points[i+1][0], points[i+1][1]
                )
                if dist < min_dist:
                    min_dist = dist
                if dist <= CABLE_PROXIMITY_KM:
                    near_cables.add(cable['slug'])
                    near_count += 1
                    is_near_cable = True
                    break
            if is_near_cable:
                break

        # 記錄低速徘徊時間戳（海纜鄰近 + 速度 < 8 knots）
        if is_near_cable and pt.get('speed', 99) < CABLE_LOITER_MAX_KNOTS:
            ts = pt.get('t', '')
            if ts:
                loiter_slow_timestamps.append(ts)

    # 從實際時間戳計算徘徊時數
    loiter_hours = 0.0
    if len(loiter_slow_timestamps) >= 2:
        try:
            t_first = datetime.fromisoformat(
                loiter_slow_timestamps[0].replace('Z', '+00:00'))
            t_last = datetime.fromisoformat(
                loiter_slow_timestamps[-1].replace('Z', '+00:00'))
            loiter_hours = (t_last - t_first).total_seconds() / 3600
        except (ValueError, AttributeError):
            # 回退：用快照數 × 平均間隔估算
            loiter_hours = len(loiter_slow_timestamps) * 2.0
    is_loitering = loiter_hours >= CABLE_LOITER_HOURS

    is_near = len(near_cables) > 0
    return is_near, {
        'cables_nearby': list(near_cables),
        'min_distance_km': round(min_dist, 2) if min_dist < float('inf') else None,
        'proximity_points': near_count,
        'loiter_slow_hours': round(loiter_hours, 1),
        'loiter_triggered': is_loitering,
    }


def check_zigzag_pattern(track_points):
    """
    檢測 Z 字型移動模式（頻繁大幅改變航向）
    使用 calc_bearing() 從實際位置計算航向，避免依賴可能不準確的 AIS heading。
    回傳: (is_zigzag, details)
    """
    if len(track_points) < 4:
        return False, {}

    # 從連續位置計算實際航向（排除靜止點和距離過近的點）
    bearings = []
    for i in range(1, len(track_points)):
        p1, p2 = track_points[i - 1], track_points[i]
        lat1, lon1 = p1.get('lat'), p1.get('lon')
        lat2, lon2 = p2.get('lat'), p2.get('lon')
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            continue
        dist = haversine_km(lat1, lon1, lat2, lon2)
        if dist < 0.1:  # 移動不足 100m，跳過
            continue
        bearings.append(calc_bearing(lat1, lon1, lat2, lon2))

    if len(bearings) < 4:
        return False, {}

    # 計算航向變化
    turns = 0
    heading_changes = []
    for i in range(1, len(bearings)):
        delta = angular_diff(bearings[i], bearings[i - 1])
        heading_changes.append(delta)
        if delta >= ZIGZAG_HEADING_CHANGE_DEG:
            turns += 1

    avg_change = sum(heading_changes) / len(heading_changes) if heading_changes else 0
    is_zigzag = turns >= ZIGZAG_MIN_TURNS

    return is_zigzag, {
        'turn_count': turns,
        'avg_heading_change': round(avg_change, 1),
        'threshold': f'>={ZIGZAG_MIN_TURNS} turns of >={ZIGZAG_HEADING_CHANGE_DEG}°',
    }


def check_depth_200m_activity(track_points):
    """
    檢查船隻是否在 200m 等深線附近活動
    回傳: (is_near_contour, details)
    """
    if not track_points:
        return False, {}

    near_count = 0
    min_dist = float('inf')

    for pt in track_points:
        plat = pt.get('lat')
        plon = pt.get('lon')
        if plat is None or plon is None:
            continue

        # 計算到等深線各段的最短距離
        for i in range(len(DEPTH_200M_CONTOUR) - 1):
            dist = point_to_segment_distance_km(
                plat, plon,
                DEPTH_200M_CONTOUR[i][0], DEPTH_200M_CONTOUR[i][1],
                DEPTH_200M_CONTOUR[i+1][0], DEPTH_200M_CONTOUR[i+1][1]
            )
            if dist < min_dist:
                min_dist = dist
            if dist <= DEPTH_200M_CONTOUR_KM:
                near_count += 1
                break

    total = len([p for p in track_points if p.get('lat') is not None])
    ratio = near_count / max(total, 1)
    is_near = ratio >= 0.3  # 30% 以上的時間在等深線附近

    return is_near, {
        'contour_proximity_ratio': round(ratio, 3),
        'contour_points': near_count,
        'total_points': total,
        'min_distance_km': round(min_dist, 2) if min_dist < float('inf') else None,
    }


def analyze_ais_anomalies(profile, identity_events=None):
    """
    AIS 異常偵測
    - 多次變更船名
    - Going dark（AIS 訊號消失再出現）
    - 身分變更事件（來自 identity_events.json）
    """
    anomalies = []

    # 船名變更偵測
    name_count = len(profile.get('names_seen', []))
    if name_count >= NAME_CHANGE_THRESHOLD:
        anomalies.append({
            'type': 'name_change',
            'description': f'使用 {name_count} 個不同船名',
            'names': profile['names_seen'],
            'severity': 'high' if name_count >= 5 else 'medium'
        })

    # Going dark 偵測
    timestamps = profile.get('last_seen_timestamps', [])
    dark_events = 0
    if len(timestamps) >= 2:
        for i in range(1, len(timestamps)):
            try:
                t1 = datetime.fromisoformat(timestamps[i-1].replace('Z', '+00:00'))
                t2 = datetime.fromisoformat(timestamps[i].replace('Z', '+00:00'))
                gap_hours = (t2 - t1).total_seconds() / 3600
                if gap_hours > GOING_DARK_GAP_HOURS:
                    dark_events += 1
            except (ValueError, KeyError, AttributeError):
                continue

    if dark_events > 0:
        anomalies.append({
            'type': 'going_dark',
            'description': f'AIS 訊號消失 {dark_events} 次',
            'count': dark_events,
            'severity': 'high' if dark_events >= 3 else 'medium'
        })

    # 船型變更偵測
    types_seen = profile.get('types_seen', [])
    real_types = [t for t in types_seen if t not in ('unknown', 'other')]
    if len(real_types) >= 2:
        anomalies.append({
            'type': 'type_change',
            'description': f'船型變更: {" → ".join(real_types)}',
            'types': real_types,
            'severity': 'medium'
        })

    # 身分變更事件偵測
    if identity_events:
        event_count = len(identity_events)
        has_multi = any(ev.get('multi_field') for ev in identity_events)

        if event_count > 0:
            severity = 'high' if event_count >= 3 or has_multi else 'medium'
            field_changes = []
            for ev in identity_events:
                for ch in ev.get('changes', []):
                    field_changes.append(f"{ch['field']}: {ch['old']} → {ch['new']}")
            anomalies.append({
                'type': 'identity_change',
                'description': f'7 天內 {event_count} 次身分變更',
                'count': event_count,
                'multi_field': has_multi,
                'details': field_changes[:10],
                'severity': severity,
            })

    return anomalies


def load_identity_events():
    """載入身分變更事件，按 MMSI 分組，僅保留近 7 天"""
    if not IDENTITY_EVENTS_FILE.exists():
        return {}
    try:
        with open(IDENTITY_EVENTS_FILE, 'r', encoding='utf-8') as f:
            events = json.load(f)
    except Exception:
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    by_mmsi = {}
    for ev in events:
        try:
            ts = datetime.fromisoformat(ev['timestamp'].replace('Z', '+00:00'))
            if ts < cutoff:
                continue
        except (ValueError, KeyError):
            continue
        mmsi = ev.get('mmsi', '')
        if mmsi:
            by_mmsi.setdefault(mmsi, []).append(ev)
    return by_mmsi


def load_sanctions_list():
    """載入 UN 制裁船舶清單，回傳 IMO set 與 name set 和詳細資料 dict"""
    if not SANCTIONS_FILE.exists():
        return {}, set(), set()
    try:
        with open(SANCTIONS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}, set(), set()

    by_imo = {}
    imo_set = set()
    name_set = set()
    for v in data.get('vessels', []):
        imo = v.get('imo', '')
        name = v.get('name', '').upper().strip()
        if imo:
            imo_set.add(imo)
            by_imo[imo] = v
        if name:
            name_set.add(name)
    return by_imo, imo_set, name_set


def load_ship_transfers():
    """
    載入 ship_transfers.json，回傳 dict: {mmsi: {'count': N, 'suspicious': bool}}
    用於可疑計分中的 STS 旁靠加分。
    """
    if not SHIP_TRANSFERS_FILE.exists():
        print("⚠️ ship_transfers.json 不存在，跳過 STS 加分")
        return {}

    try:
        with open(SHIP_TRANSFERS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}

    sts_map = {}  # mmsi -> {count, suspicious}
    all_events = (data.get('active_transfers', [])
                  + data.get('history', []))

    for ev in all_events:
        is_susp = ev.get('classification') == 'suspicious'
        for vkey in ('vessel1', 'vessel2'):
            mmsi = str(ev.get(vkey, {}).get('mmsi', ''))
            if not mmsi:
                continue
            if mmsi not in sts_map:
                sts_map[mmsi] = {'count': 0, 'suspicious': False}
            sts_map[mmsi]['count'] += 1
            if is_susp:
                sts_map[mmsi]['suspicious'] = True

    print(f"🚢 STS 旁靠紀錄: {len(sts_map)} 艘船 "
          f"({sum(1 for v in sts_map.values() if v['suspicious'])} 艘涉及可疑旁靠)")
    return sts_map


def load_itu_mars_cache():
    """
    載入 ITU MARS 快取資料（由 lookup_itu_mars.py 建立）。
    回傳 dict: {mmsi: {ship_name, call_sign, administration, imo_number, ...}}
    """
    if not ITU_MARS_CACHE.exists():
        return {}
    try:
        with open(ITU_MARS_CACHE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        found = {k: v for k, v in data.items() if v.get('found')}
        print(f"🏛️ ITU MARS 快取: {len(found)} 筆有效記錄")
        return found
    except Exception:
        return {}


def check_itu_mars_mismatch(profile, mars_record):
    """
    比對 AIS 回報資訊 vs ITU MARS 官方登記資料。
    偵測船名不符、管理國/船旗不一致等身分偽造徵兆。
    回傳: (has_mismatch, details)
    """
    if not mars_record or not mars_record.get('found'):
        return False, {}

    mismatches = []
    details = {'mars_record': mars_record}

    # 船名比對
    mars_name = (mars_record.get('ship_name') or '').upper().strip()
    ais_names = [n.upper().strip() for n in profile.get('names_seen', []) if n]

    if mars_name and ais_names:
        # 若 AIS 使用的所有船名都與 MARS 登記名不同
        if mars_name not in ais_names:
            mismatches.append({
                'field': 'ship_name',
                'mars': mars_name,
                'ais': ais_names,
                'description': f'船名不符：登記 {mars_name}，AIS 使用 {", ".join(ais_names[:3])}'
            })

    # 管理國 vs MMSI MID 比對
    mars_admin = (mars_record.get('administration') or '').strip()
    mmsi = profile.get('mmsi', '')
    if mars_admin and len(mmsi) >= 3:
        # MID 前三碼對應的常見管理國縮寫
        # 簡單比對：MARS administration 應與 MMSI MID 對應的國家一致
        # 這裡記錄供人工判讀，不自動判定是否不符
        details['mars_administration'] = mars_admin

    # IMO 比對
    mars_imo = (mars_record.get('imo_number') or '').strip()
    ais_imo = (profile.get('last_imo') or '').strip()
    if mars_imo and ais_imo and mars_imo != ais_imo:
        mismatches.append({
            'field': 'imo_number',
            'mars': mars_imo,
            'ais': ais_imo,
            'description': f'IMO不符：登記 {mars_imo}，AIS 回報 {ais_imo}'
        })

    # 呼號比對
    mars_cs = (mars_record.get('call_sign') or '').upper().strip()
    ais_cs = (profile.get('last_callsign') or '').upper().strip()
    if mars_cs and ais_cs and mars_cs != ais_cs:
        mismatches.append({
            'field': 'call_sign',
            'mars': mars_cs,
            'ais': ais_cs,
            'description': f'呼號不符：登記 {mars_cs}，AIS 回報 {ais_cs}'
        })

    details['mismatches'] = mismatches
    return len(mismatches) > 0, details


def load_track_history():
    """載入 tier-1 + tier-2 航跡，按 MMSI 組織航跡"""
    tracks = {}  # mmsi -> [points]

    # Tier-1: CN fishing + suspicious
    # Tier-2: cargo, tanker, LNG, identity-changed
    track_sources = [
        ("tier-1", [Path("docs") / "ais_track_history.json", TRACK_HISTORY_FILE]),
        ("tier-2", [Path("docs") / "ais_track_commercial.json", TRACK_COMMERCIAL_FILE]),
    ]

    for tier_label, candidates in track_sources:
        for path in candidates:
            if path.exists():
                print(f"📂 Reading {tier_label} track history: {path}")
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for entry in data:
                    ts = entry.get('timestamp', '')
                    for v in entry.get('vessels', []):
                        mmsi = v.get('mmsi')
                        if not mmsi:
                            continue
                        if mmsi not in tracks:
                            tracks[mmsi] = []
                        tracks[mmsi].append({
                            't': ts,
                            'lat': v.get('lat'),
                            'lon': v.get('lon'),
                            'speed': v.get('speed', 0),
                            'heading': v.get('heading'),
                        })
                break  # found this tier, skip fallback path
        else:
            print(f"⚠️ {tier_label} track history not found")

    print(f"📊 Track history: {len(tracks)} vessels")
    return tracks


def classify_vessel(profile, track_points, identity_events=None,
                     sanctions_match=None, mars_record=None,
                     sts_record=None):
    """
    綜合分類單一船隻的可疑程度
    標準：海纜鄰近 + Z字型 + 200m等深線 + AIS變更 + UN制裁
          + AIS偽訊號 + ITU MARS 登記比對 + STS旁靠
    分數依船型乘數調整：商船 ×1.0、漁船 ×0.2、其他 ×0.5
    """
    mmsi = profile['mmsi']

    classification = {
        'mmsi': mmsi,
        'names': profile.get('names_seen', []),
        'total_snapshots': profile.get('total_snapshots', 0),
        'cable_proximity': False,
        'cable_loitering': False,
        'non_top10_flag': False,
        'zigzag_pattern': False,
        'depth_200m_activity': False,
        'sanctioned': False,
        'spoof_impossible_physics': False,
        'spoof_box_pattern': False,
        'spoof_circle_pattern': False,
        'itu_mars_mismatch': False,
        'ais_anomalies': [],
        'risk_level': 'normal',
        'flags': [],
    }

    # ── 排除規則檢查（漁網、浮標、信標等非船舶設備）──
    # 提前檢查，避免對非船舶設備執行昂貴分析
    all_names = profile.get('names_seen', [])
    excluded, matched_rules = check_exclusion_rules(mmsi, all_names)
    classification['excluded'] = excluded
    if excluded:
        if matched_rules:
            classification['exclusion_rules'] = matched_rules
        reasons = ' + '.join(r['label'] for r in matched_rules)
        classification['risk_level'] = 'normal'
        classification['risk_score'] = 0
        classification['raw_score'] = 0
        classification['suspicious'] = False
        classification['vessel_type'] = 'unknown'
        classification['type_multiplier'] = 0
        classification['flags'] = [f'排除: {reasons}']
        return classification

    # ── Criterion 1: 海纜鄰近活動 ──
    if track_points:
        cable_near, cable_details = check_cable_proximity(track_points)
        classification['cable_proximity'] = cable_near
        classification['cable_details'] = cable_details
        if cable_near:
            cables_str = ', '.join(cable_details.get('cables_nearby', [])[:3])
            classification['flags'].append(f'海纜鄰近活動：{cables_str}')

        # Criterion 1b: 海纜鄰近低速徘徊 (>3hr, <8kn)
        if cable_details.get('loiter_triggered'):
            classification['cable_loitering'] = True
            hrs = cable_details.get('loiter_slow_hours', 0)
            classification['flags'].append(
                f'海纜低速徘徊：{hrs}h (<{CABLE_LOITER_MAX_KNOTS}kn)'
            )

    # ── Criterion 2: Z 字型移動 ──
    if track_points:
        zigzag, zigzag_details = check_zigzag_pattern(track_points)
        classification['zigzag_pattern'] = zigzag
        classification['zigzag_details'] = zigzag_details
        if zigzag:
            classification['flags'].append(
                f'Z字型移動模式：{zigzag_details["turn_count"]} 次大幅轉向'
            )

    # ── Criterion 3: 200m 等深線活動 ──
    if track_points:
        depth_near, depth_details = check_depth_200m_activity(track_points)
        classification['depth_200m_activity'] = depth_near
        classification['depth_200m_details'] = depth_details
        if depth_near:
            pct = round(depth_details['contour_proximity_ratio'] * 100)
            classification['flags'].append(f'200m等深線活動：{pct}% 時間')

    # ── Criterion 4: AIS 異常 ──
    anomalies = analyze_ais_anomalies(profile, identity_events)
    classification['ais_anomalies'] = anomalies
    if anomalies:
        classification['flags'].extend([a['description'] for a in anomalies])

    # ── Criterion 5: 非前十大船旗國 ──
    mid = mmsi[:3] if len(mmsi) >= 3 else ''
    if mid and mid not in TOP_10_FLAG_MIDS:
        classification['non_top10_flag'] = True
        classification['flags'].append(f'非前十大船旗國 (MID {mid})')

    # ── Criterion 6: UN 制裁清單 ──
    if sanctions_match:
        classification['sanctioned'] = True
        classification['sanction_info'] = sanctions_match
        res = sanctions_match.get('resolution', '1718')
        measures = ', '.join(sanctions_match.get('measures', []))
        classification['flags'].append(
            f'⚠️ UN 制裁船舶 (UNSCR {res}: {measures})'
        )

    # ── Criterion 7: AIS 偽訊號偵測 (Spoofing) ──
    if track_points:
        physics, physics_details = check_impossible_physics(track_points)
        classification['spoof_impossible_physics'] = physics
        classification['spoof_physics_details'] = physics_details
        if physics:
            parts = []
            if physics_details.get('teleport_count'):
                parts.append(f'{physics_details["teleport_count"]}次瞬移')
            if physics_details.get('speed_mismatch_count'):
                parts.append(f'速度不符{physics_details["speed_mismatch_count"]}次')
            if physics_details.get('bearing_mismatch_count'):
                parts.append(f'航向不符{physics_details["bearing_mismatch_count"]}次')
            classification['flags'].append(
                f'AIS異常物理：{", ".join(parts)}'
            )

        box, box_details = check_box_pattern(track_points)
        classification['spoof_box_pattern'] = box
        classification['spoof_box_details'] = box_details
        if box:
            classification['flags'].append(
                f'AIS方形軌跡：{box_details["right_angle_turns"]}次直角轉彎 '
                f'(bbox {box_details["bounding_box_km"]}km)'
            )

        circle, circle_details = check_circle_pattern(track_points)
        classification['spoof_circle_pattern'] = circle
        classification['spoof_circle_details'] = circle_details
        if circle:
            classification['flags'].append(
                f'AIS圓形軌跡：半徑{circle_details["mean_radius_km"]}km '
                f'CV={circle_details["radius_cv"]} '
                f'弧度{circle_details["arc_coverage_deg"]}°'
            )

    # ── Criterion 8: ITU MARS 登記比對 ──
    if mars_record:
        has_mismatch, mars_details = check_itu_mars_mismatch(
            profile, mars_record)
        classification['itu_mars_mismatch'] = has_mismatch
        classification['itu_mars_details'] = mars_details
        if has_mismatch:
            for m in mars_details.get('mismatches', []):
                classification['flags'].append(
                    f'ITU登記不符：{m["description"]}'
                )

    # ── 判定主要船型 ──
    types_seen = profile.get('types_seen', [])
    # 取最後一個非 unknown/other 的船型；否則 unknown
    vessel_type = 'unknown'
    for t in reversed(types_seen):
        if t not in ('unknown', 'other'):
            vessel_type = t
            break
    classification['vessel_type'] = vessel_type
    type_mult = VESSEL_TYPE_MULTIPLIER.get(vessel_type, 0.5)
    classification['type_multiplier'] = type_mult

    # ── 風險計分 ──
    raw_score = 0

    # ── 基礎行為分（單獨不構成可疑）──
    if classification['cable_proximity']:
        raw_score += 2  # 海纜 5km 內
    if classification['cable_loitering']:
        raw_score += 3  # 海纜低速徘徊 >3hr <8kn
    if classification['zigzag_pattern']:
        raw_score += 1  # Z字型
    if classification['depth_200m_activity']:
        raw_score += 1
    if classification['non_top10_flag']:
        raw_score += 1  # 非前十大船旗國

    # ── 組合加分（多重指標交叉 = 高度可疑）──
    if classification['cable_proximity'] and classification['zigzag_pattern']:
        raw_score += 3  # 海纜鄰近 + Z字型 = 可能拖錨
    if classification['cable_proximity'] and classification['cable_loitering']:
        raw_score += 2  # 海纜鄰近 + 長時間徘徊

    # ── 套用船型乘數（商船 ×1.0, 漁船 ×0.2, 其他 ×0.5）──
    score = raw_score * type_mult

    # ── 高威脅指標（不受船型乘數影響）──
    if classification['sanctioned']:
        score += 8  # UN 制裁船舶 — 最高優先
    for a in anomalies:
        if a['severity'] == 'high':
            score += 3  # 嚴重 AIS 異常（多次船名變更等）
        else:
            score += 1

    # ── AIS 偽訊號（不受船型乘數影響，每項 +4）──
    if classification['spoof_impossible_physics']:
        score += 4
    if classification['spoof_box_pattern']:
        score += 4
    if classification['spoof_circle_pattern']:
        score += 4
    # 偽訊號 + 海纜鄰近 = 蓄意隱匿
    spoofing = (classification['spoof_impossible_physics'] or
                classification['spoof_box_pattern'] or
                classification['spoof_circle_pattern'])
    if spoofing and classification['cable_proximity']:
        score += 3

    # ── ITU MARS 登記不符（不受船型乘數影響）──
    if classification['itu_mars_mismatch']:
        score += 3

    # ── STS 旁靠加分（不受船型乘數影響）──
    if sts_record:
        classification['sts_transfer'] = True
        classification['sts_count'] = sts_record['count']
        classification['sts_suspicious'] = sts_record['suspicious']
        if sts_record['suspicious']:
            score += STS_SUSPICIOUS_SCORE
            classification['flags'].append(
                f'可疑旁靠 (STS)：{sts_record["count"]} 次')
        else:
            score += STS_ANY_SCORE
            classification['flags'].append(
                f'旁靠紀錄：{sts_record["count"]} 次')

    # 四捨五入為整數
    score = round(score)

    # ── 風險等級 ──
    if score >= 12:
        classification['risk_level'] = 'critical'
    elif score >= 8:
        classification['risk_level'] = 'high'
    elif score >= 5:
        classification['risk_level'] = 'medium'

    classification['raw_score'] = raw_score
    classification['risk_score'] = score
    classification['suspicious'] = score >= 8

    # 附加位置資訊
    if track_points:
        last = track_points[-1]
        classification['last_lat'] = last.get('lat')
        classification['last_lon'] = last.get('lon')
        classification['last_seen'] = last.get('t')

    return classification


def main():
    print("=" * 60)
    print("🔍 海底電纜威脅偵測 — 可疑船隻分析")
    print("   Cable Threat Detection Engine")
    print("=" * 60)
    print(f"執行時間: {datetime.now(timezone.utc).isoformat()}")

    # 載入資料
    id_events_by_mmsi = load_identity_events()
    id_event_count = sum(len(v) for v in id_events_by_mmsi.values())
    print(f"🔄 身分變更事件: {id_event_count} 筆 ({len(id_events_by_mmsi)} 艘船)")

    # 載入 UN 制裁清單
    sanctions_by_imo, sanctions_imo_set, sanctions_name_set = load_sanctions_list()
    print(f"🚫 UN 制裁船舶: {len(sanctions_imo_set)} 艘")

    # 載入船隻 profile（用於 AIS 異常偵測）
    profiles = {}
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            profiles_data = json.load(f)
        if isinstance(profiles_data, dict):
            profiles = profiles_data
    print(f"📋 船隻 profiles: {len(profiles)}")

    # 載入 ITU MARS 快取（用於交叉比對船舶登記資料）
    mars_cache = load_itu_mars_cache()

    # 載入 STS 旁靠紀錄
    sts_map = load_ship_transfers()

    # 載入航跡歷史（用於海纜鄰近、Z字型、等深線分析）
    tracks = load_track_history()

    # 預載海纜資料
    load_cable_segments()

    # 合併所有 MMSI（profile + track 的聯集）
    all_mmsi = set(profiles.keys()) | set(tracks.keys())
    print(f"\n📊 分析 {len(all_mmsi)} 艘船隻...")

    classifications = []
    suspicious_vessels = []
    excluded_vessels = []

    for mmsi in all_mmsi:
        profile = profiles.get(mmsi, {
            'mmsi': mmsi,
            'names_seen': [],
            'types_seen': [],
            'total_snapshots': 0,
        })
        if 'mmsi' not in profile:
            profile['mmsi'] = mmsi

        track_pts = tracks.get(mmsi, [])
        id_events = id_events_by_mmsi.get(mmsi)

        # 檢查是否在 UN 制裁清單中（比對 IMO 和船名）
        sanction_hit = None
        imo = profile.get('last_imo', '')
        if imo and imo in sanctions_imo_set:
            sanction_hit = sanctions_by_imo.get(imo)
        if not sanction_hit:
            for name in profile.get('names_seen', []):
                if name.upper().strip() in sanctions_name_set:
                    # 名稱匹配 — 找到對應的制裁條目
                    for sv in sanctions_by_imo.values():
                        if sv.get('name', '').upper().strip() == name.upper().strip():
                            sanction_hit = sv
                            break
                    break

        mars_rec = mars_cache.get(mmsi)
        sts_rec = sts_map.get(mmsi)
        result = classify_vessel(profile, track_pts, id_events,
                                 sanctions_match=sanction_hit,
                                 mars_record=mars_rec,
                                 sts_record=sts_rec)
        if result.get('excluded'):
            excluded_vessels.append(result)
        else:
            classifications.append(result)
            if result['suspicious']:
                suspicious_vessels.append(result)

    # 按風險分數排序
    suspicious_vessels.sort(key=lambda x: x['risk_score'], reverse=True)
    classifications.sort(key=lambda x: x.get('risk_score', 0), reverse=True)

    # Top 10% 高風險船隻數量（排除後的船隻，取前 10%）
    non_excluded_count = len(classifications)
    top_10pct_cutoff = max(1, non_excluded_count // 10)
    top_10pct_vessels = classifications[:top_10pct_cutoff]
    # 只計算 score > 0 的（避免把大量 0 分船隻算進去）
    top_10pct_with_score = [v for v in top_10pct_vessels if v.get('risk_score', 0) > 0]

    # 排除規則統計
    exclusion_stats = {}
    for ev in excluded_vessels:
        for rule in ev.get('exclusion_rules', []):
            rid = rule['id']
            exclusion_stats[rid] = exclusion_stats.get(rid, 0) + 1

    # 統計
    risk_counts = {'critical': 0, 'high': 0, 'medium': 0, 'normal': 0}
    cable_count = 0
    loiter_count = 0
    zigzag_count = 0
    depth_count = 0
    anomaly_count = 0
    non_top10_count = 0
    sanctioned_count = 0
    spoof_physics_count = 0
    spoof_box_count = 0
    spoof_circle_count = 0
    mars_mismatch_count = 0
    sts_transfer_count = 0

    for c in classifications:
        risk_counts[c['risk_level']] += 1
        if c.get('cable_proximity'):
            cable_count += 1
        if c.get('cable_loitering'):
            loiter_count += 1
        if c.get('zigzag_pattern'):
            zigzag_count += 1
        if c.get('depth_200m_activity'):
            depth_count += 1
        if c.get('ais_anomalies'):
            anomaly_count += 1
        if c.get('non_top10_flag'):
            non_top10_count += 1
        if c.get('sanctioned'):
            sanctioned_count += 1
        if c.get('spoof_impossible_physics'):
            spoof_physics_count += 1
        if c.get('spoof_box_pattern'):
            spoof_box_count += 1
        if c.get('spoof_circle_pattern'):
            spoof_circle_count += 1
        if c.get('itu_mars_mismatch'):
            mars_mismatch_count += 1
        if c.get('sts_transfer'):
            sts_transfer_count += 1

    output = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'methodology': 'Submarine Cable Threat Detection',
        'criteria': {
            'cable_proximity_km': CABLE_PROXIMITY_KM,
            'cable_loiter_hours': CABLE_LOITER_HOURS,
            'cable_loiter_max_knots': CABLE_LOITER_MAX_KNOTS,
            'zigzag_min_turns': ZIGZAG_MIN_TURNS,
            'zigzag_heading_change_deg': ZIGZAG_HEADING_CHANGE_DEG,
            'depth_200m_contour_km': DEPTH_200M_CONTOUR_KM,
            'name_change_threshold': NAME_CHANGE_THRESHOLD,
            'spoof_teleport_kmh': SPOOF_TELEPORT_KMH,
            'spoof_box_angle_tolerance': SPOOF_BOX_ANGLE_TOLERANCE,
            'spoof_circle_radius_cv': SPOOF_CIRCLE_RADIUS_CV,
            'vessel_type_multiplier': VESSEL_TYPE_MULTIPLIER,
            'sts_suspicious_score': STS_SUSPICIOUS_SCORE,
            'sts_any_score': STS_ANY_SCORE,
        },
        'exclusion_rules': [
            {'id': r['id'], 'label': r['label']} for r in EXCLUSION_RULES
        ],
        'summary': {
            'total_analyzed': len(all_mmsi),
            'excluded_count': len(excluded_vessels),
            'exclusion_breakdown': exclusion_stats,
            'suspicious_count': len(suspicious_vessels),
            'top_10pct_count': len(top_10pct_with_score),
            'top_10pct_min_score': top_10pct_with_score[-1]['risk_score'] if top_10pct_with_score else 0,
            'cable_proximity_triggered': cable_count,
            'cable_loitering_triggered': loiter_count,
            'zigzag_pattern_detected': zigzag_count,
            'depth_200m_activity': depth_count,
            'ais_anomaly_detected': anomaly_count,
            'non_top10_flag': non_top10_count,
            'sanctioned_vessels': sanctioned_count,
            'spoof_impossible_physics': spoof_physics_count,
            'spoof_box_pattern': spoof_box_count,
            'spoof_circle_pattern': spoof_circle_count,
            'itu_mars_mismatch': mars_mismatch_count,
            'sts_transfer': sts_transfer_count,
            'risk_distribution': risk_counts,
        },
        'suspicious_vessels': suspicious_vessels[:50],
        'all_classifications': classifications[:200],
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n📋 分析結果:")
    print(f"   分析船隻數: {len(all_mmsi)}")
    print(f"   排除 (非船舶設備): {len(excluded_vessels)}")
    if exclusion_stats:
        for rid, cnt in sorted(exclusion_stats.items(), key=lambda x: -x[1]):
            print(f"     - {rid}: {cnt}")
    print(f"   可疑船隻 (score ≥ 8): {len(suspicious_vessels)}")
    print(f"   海纜鄰近: {cable_count}")
    print(f"   海纜低速徘徊 (>3hr <8kn): {loiter_count}")
    print(f"   Z字型移動: {zigzag_count}")
    print(f"   200m等深線: {depth_count}")
    print(f"   AIS 異常: {anomaly_count}")
    print(f"   非前十大船旗: {non_top10_count}")
    print(f"   UN 制裁匹配: {sanctioned_count}")
    print(f"   偽訊號-異常物理: {spoof_physics_count}")
    print(f"   偽訊號-方形軌跡: {spoof_box_count}")
    print(f"   偽訊號-圓形軌跡: {spoof_circle_count}")
    print(f"   ITU MARS不符: {mars_mismatch_count}")
    print(f"   STS旁靠涉入: {sts_transfer_count}")
    print(f"   風險分布: {risk_counts}")
    print(f"\n📁 結果已輸出至: {OUTPUT_FILE}")


if __name__ == '__main__':
    main()
