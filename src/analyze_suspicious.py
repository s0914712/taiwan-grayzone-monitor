#!/usr/bin/env python3
"""
================================================================================
海底電纜威脅偵測 — 可疑船隻分析引擎
Suspicious Vessel Analysis: Submarine Cable Threat Detection
================================================================================

偵測邏輯（針對海纜破壞威脅）：
  1. 海底電纜鄰近活動 (Cable Proximity)
     - 船隻航跡經過海纜路線 5 公里內
  2. Z 字型移動模式 (Zigzag Pattern)
     - 頻繁改變航向，疑似拖錨或破壞行為
  3. 200 公尺等深線活動 (Continental Shelf Edge)
     - 在大陸棚邊緣活動，海纜密集區
  4. AIS 身分變更 (Identity Manipulation)
     - 變更船名、呼號、IMO 等識別資訊
================================================================================
"""

import json
import math
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path("data")
HISTORY_FILE = DATA_DIR / "vessel_profiles.json"
TRACK_HISTORY_FILE = DATA_DIR / "ais_track_history.json"
CABLE_GEO_FILE = DATA_DIR / "cable-geo.json"
IDENTITY_EVENTS_FILE = DATA_DIR / "identity_events.json"
SANCTIONS_FILE = DATA_DIR / "un_sanctions_vessels.json"
OUTPUT_FILE = DATA_DIR / "suspicious_vessels.json"

# ── 門檻設定 ────────────────────────────────────────────
CABLE_PROXIMITY_KM = 5.0          # 海纜 5 公里內視為鄰近
CABLE_LOITER_HOURS = 3.0          # 海纜鄰近低速徘徊 > 3 小時
CABLE_LOITER_MAX_KNOTS = 8.0      # 低速定義 < 8 knots
ZIGZAG_HEADING_CHANGE_DEG = 45    # 航向變化 > 45° 視為一次轉向
ZIGZAG_MIN_TURNS = 3              # 至少 3 次轉向才算 Z 字型
DEPTH_200M_CONTOUR_KM = 10.0      # 200m 等深線緩衝區寬度
NAME_CHANGE_THRESHOLD = 2         # 船名變更 ≥ 2 次
GOING_DARK_GAP_HOURS = 18         # AIS 消失 > 18 小時

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
                _cable_segments.append({
                    'slug': slug,
                    'points': tw_points
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


def check_cable_proximity(track_points):
    """
    檢查船隻航跡是否經過海纜附近
    同時偵測低速徘徊（<8kn 在海纜 5km 內超過 3 小時）
    回傳: (is_near, details)
    """
    cables = load_cable_segments()
    if not cables:
        return False, {}

    near_cables = set()
    min_dist = float('inf')
    near_count = 0
    loiter_slow_count = 0  # 海纜鄰近且低速的快照數

    for pt in track_points:
        plat = pt.get('lat')
        plon = pt.get('lon')
        if plat is None or plon is None:
            continue

        is_near_cable = False
        for cable in cables:
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

        # 計算低速徘徊（海纜鄰近 + 速度 < 8 knots）
        if is_near_cable and pt.get('speed', 99) < CABLE_LOITER_MAX_KNOTS:
            loiter_slow_count += 1

    # 估算徘徊時數（每個快照間隔約 2 小時）
    loiter_hours = loiter_slow_count * 2.0
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
    回傳: (is_zigzag, details)
    """
    if len(track_points) < 4:
        return False, {}

    # 計算連續航向變化
    headings = []
    for pt in track_points:
        h = pt.get('heading')
        if h is not None and pt.get('speed', 0) > 0.5:  # 排除靜止船隻
            headings.append(h)

    if len(headings) < 4:
        return False, {}

    # 計算航向變化
    turns = 0
    heading_changes = []
    for i in range(1, len(headings)):
        delta = abs(headings[i] - headings[i-1])
        if delta > 180:
            delta = 360 - delta
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
    snapshots = profile.get('snapshots', [])
    dark_events = 0
    if len(snapshots) >= 2:
        for i in range(1, len(snapshots)):
            try:
                t1 = datetime.fromisoformat(snapshots[i-1]['time'].replace('Z', '+00:00'))
                t2 = datetime.fromisoformat(snapshots[i]['time'].replace('Z', '+00:00'))
                gap_hours = (t2 - t1).total_seconds() / 3600
                if gap_hours > GOING_DARK_GAP_HOURS:
                    dark_events += 1
            except (ValueError, KeyError):
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


def load_track_history():
    """載入 ais_track_history.json，按 MMSI 組織航跡"""
    # Try docs/ first (may be more up-to-date), then data/
    for path in [Path("docs") / "ais_track_history.json", TRACK_HISTORY_FILE]:
        if path.exists():
            print(f"📂 Reading track history: {path}")
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            break
    else:
        print("⚠️ ais_track_history.json not found")
        return {}

    tracks = {}  # mmsi -> [points]
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

    print(f"📊 Track history: {len(tracks)} vessels")
    return tracks


def classify_vessel(profile, track_points, identity_events=None,
                     sanctions_match=None):
    """
    綜合分類單一船隻的可疑程度
    新標準：海纜鄰近 + Z字型 + 200m等深線 + AIS變更 + UN制裁
    """
    mmsi = profile['mmsi']

    classification = {
        'mmsi': mmsi,
        'names': profile.get('names_seen', []),
        'flags_used': [],
        'total_snapshots': profile.get('total_snapshots', 0),
        'cable_proximity': False,
        'cable_loitering': False,
        'non_top10_flag': False,
        'zigzag_pattern': False,
        'depth_200m_activity': False,
        'sanctioned': False,
        'ais_anomalies': [],
        'risk_level': 'normal',
        'flags': [],
    }

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

    # ── 風險計分 ──
    score = 0
    # 海纜鄰近 + Z字型 = 強烈可疑（可能拖錨破壞）
    if classification['cable_proximity']:
        score += 3
    if classification['cable_loitering']:
        score += 2  # 海纜低速徘徊 >3hr <8kn
    if classification['zigzag_pattern']:
        score += 2
    # 海纜鄰近 + Z字型組合加分
    if classification['cable_proximity'] and classification['zigzag_pattern']:
        score += 2
    if classification['depth_200m_activity']:
        score += 1
    if classification['non_top10_flag']:
        score += 1  # 非前十大船旗國
    if classification['sanctioned']:
        score += 5  # UN 制裁船舶 — 最高優先
    for a in anomalies:
        if a['severity'] == 'high':
            score += 2
        else:
            score += 1

    if score >= 7:
        classification['risk_level'] = 'critical'
    elif score >= 4:
        classification['risk_level'] = 'high'
    elif score >= 2:
        classification['risk_level'] = 'medium'

    classification['risk_score'] = score
    classification['suspicious'] = score >= 4

    # 附加位置資訊
    if track_points:
        last = track_points[-1]
        classification['last_lat'] = last.get('lat')
        classification['last_lon'] = last.get('lon')
        classification['last_seen'] = last.get('t')
    else:
        snapshots = profile.get('snapshots', [])
        if snapshots:
            last = snapshots[-1]
            classification['last_lat'] = last.get('lat')
            classification['last_lon'] = last.get('lon')
            classification['last_seen'] = last.get('time')

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

    # 載入航跡歷史（用於海纜鄰近、Z字型、等深線分析）
    tracks = load_track_history()

    # 預載海纜資料
    load_cable_segments()

    # 合併所有 MMSI（profile + track 的聯集）
    all_mmsi = set(profiles.keys()) | set(tracks.keys())
    print(f"\n📊 分析 {len(all_mmsi)} 艘船隻...")

    classifications = []
    suspicious_vessels = []

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

        result = classify_vessel(profile, track_pts, id_events,
                                 sanctions_match=sanction_hit)
        if result['risk_score'] > 0:
            classifications.append(result)
        if result['suspicious']:
            suspicious_vessels.append(result)

    # 按風險分數排序
    suspicious_vessels.sort(key=lambda x: x['risk_score'], reverse=True)

    # 統計
    risk_counts = {'critical': 0, 'high': 0, 'medium': 0, 'normal': 0}
    cable_count = 0
    loiter_count = 0
    zigzag_count = 0
    depth_count = 0
    anomaly_count = 0
    non_top10_count = 0
    sanctioned_count = 0

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
        },
        'summary': {
            'total_analyzed': len(all_mmsi),
            'suspicious_count': len(suspicious_vessels),
            'cable_proximity_triggered': cable_count,
            'cable_loitering_triggered': loiter_count,
            'zigzag_pattern_detected': zigzag_count,
            'depth_200m_activity': depth_count,
            'ais_anomaly_detected': anomaly_count,
            'non_top10_flag': non_top10_count,
            'sanctioned_vessels': sanctioned_count,
            'risk_distribution': risk_counts,
        },
        'suspicious_vessels': suspicious_vessels[:50],
        'all_classifications': classifications[:200],
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n📋 分析結果:")
    print(f"   分析船隻數: {len(all_mmsi)}")
    print(f"   可疑船隻 (score ≥ 4): {len(suspicious_vessels)}")
    print(f"   海纜鄰近: {cable_count}")
    print(f"   海纜低速徘徊 (>3hr <8kn): {loiter_count}")
    print(f"   Z字型移動: {zigzag_count}")
    print(f"   200m等深線: {depth_count}")
    print(f"   AIS 異常: {anomaly_count}")
    print(f"   非前十大船旗: {non_top10_count}")
    print(f"   UN 制裁匹配: {sanctioned_count}")
    print(f"   風險分布: {risk_counts}")
    print(f"\n📁 結果已輸出至: {OUTPUT_FILE}")


if __name__ == '__main__':
    main()
