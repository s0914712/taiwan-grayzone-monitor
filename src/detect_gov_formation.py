#!/usr/bin/env python3
"""
================================================================================
公務船編隊偵測 — China Government Vessel Formation Detection
Detect ≥2 China government / research vessels operating together
(within FORMATION_RADIUS_KM) continuously for ≥FORMATION_MIN_DURATION_HOURS.
================================================================================

動機：2026-08 宜花東外海實例 — 向陽紅03（科研）與海警2502、海警1306 在
23.5-23.7°N / 122.2-122.8°E 同一方框內、同一批時戳同步東西向來回。既有的
逐船評分完全看不到這件事：科研船離海纜遠 → 海纜分 0；STS 旁靠偵測是為
油輪過駁設計的（門檻 10 公尺、<5kn），對相距數公里的護航編隊 0 命中。

本模組把「編隊」本身當成一個獨立事件型別：
  - 空間：單一鏈結分群（single-linkage），成員間距 ≤10km 即同群
  - 時間：跨快照以「共同成員 ≥2 艘」串接，間隔 >6h 即視為中斷
  - 門檻：持續 ≥6 小時才成案
  - 科研船 + 海警/海巡同框 → escorted_research（護航科考），最高關注

輸出 data/gov_formations.json，由 analyze_suspicious.py 讀取計分
（高威脅指標，**不受船型乘數影響** — 科研船的 ×0.5 會把編隊訊號抹平）。
================================================================================
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from geo_utils import haversine_km
from io_utils import atomic_write_json
import geofence

DATA_DIR = Path("data")
DOCS_DIR = Path("docs")
# tier-1 軌跡歷史含全部公務/科研船（fetch_ais_data 強制保留於 tier-1）
TRACK_HISTORY_FILE = DOCS_DIR / "ais_track_history.json"
OUTPUT_FILE = DATA_DIR / "gov_formations.json"

# ── 門檻設定 ────────────────────────────────────────────
FORMATION_RADIUS_KM = 10.0          # 成員間距 ≤10km 視為同一編隊
FORMATION_MIN_VESSELS = 2           # 至少 2 艘公務/科研船
FORMATION_MIN_DURATION_HOURS = 6.0  # 持續 ≥6 小時才成案
FORMATION_MAX_GAP_HOURS = 6.0       # 快照間隔 >6h 即斷開（AIS 常態 1-2h 取樣）
FORMATION_ACTIVE_HOURS = 24.0       # 距最新快照 24h 內視為進行中
FORMATION_MAX_SPREAD_KM = 30.0      # 群內最大兩兩距離上限 — 單一鏈結會沿著
                                    # 一串等距船「接龍」，超過此幅度不算編隊
MAX_CENTROID_POINTS = 120           # 中心軌跡輸出上限（避免 JSON 膨脹）

# ── 泊地抑制 ────────────────────────────────────────────
# 公務船的母港/基地天天停著好幾艘，港內排除（geofence）擋得掉已知港口，
# 但沿岸基地/錨地不可能列全。通用規則：整段期間中心點幾乎沒移動、且成員
# 幾乎不動 → 是碼頭或錨地，不是海上編隊作業。
FORMATION_MIN_DISPLACEMENT_KM = 2.0   # 中心點位移上限（低於此且低速即抑制）
FORMATION_BERTH_MAX_KNOTS = 1.0       # 成員速度中位數低於此視為停泊

# 公務/科研船類別（與 fetch_ais_data.classify_gov_vessel 一致）
GOV_CATEGORIES = ('coastguard', 'msa', 'rescue', 'research')
# 護航科考：科研船 + 執法船同框
ESCORT_CATEGORIES = ('coastguard', 'msa')


def gov_category(vessel):
    """取出航跡點的公務船類別，非公務船回傳 None。

    只認 fetch_ais_data.py 已寫入的旗標（tier-1 的 `gov` 欄位，或 type_name
    被覆寫成類別名），不在此重跑船名正則 — 分類邏輯只該有一份。
    """
    cat = vessel.get('gov')
    if cat in GOV_CATEGORIES:
        return cat
    tn = vessel.get('type_name')
    if tn in GOV_CATEGORIES:
        return tn
    return None


def parse_ts(value):
    """ISO 時戳 → aware datetime；失敗回傳 None。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def cluster_by_distance(vessels, radius_km=FORMATION_RADIUS_KM):
    """單一鏈結分群：成員間距 ≤radius_km 即同群。

    公務船同一時刻通常只有數十艘，O(n²) 足夠。
    回傳: [[vessel, ...], ...]（僅含 ≥FORMATION_MIN_VESSELS 艘的群）
    """
    pts = [v for v in vessels
           if v.get('lat') is not None and v.get('lon') is not None]
    n = len(pts)
    parent = list(range(n))


    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            d = haversine_km(pts[i]['lat'], pts[i]['lon'],
                             pts[j]['lat'], pts[j]['lon'])
            if d <= radius_km:
                parent[find(j)] = find(i)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(pts[i])
    return [g for g in groups.values()
            if len(g) >= FORMATION_MIN_VESSELS
            and group_radius_km(g) <= FORMATION_MAX_SPREAD_KM]


def group_radius_km(group):
    """群內最大兩兩距離（編隊展開幅度）。"""
    m = 0.0
    for i in range(len(group)):
        for j in range(i + 1, len(group)):
            m = max(m, haversine_km(group[i]['lat'], group[i]['lon'],
                                    group[j]['lat'], group[j]['lon']))
    return m


def load_gov_snapshots(track_data, exclude_ports=True):
    """tier-1 航跡歷史 → [(datetime, [公務船, ...]), ...]，依時間排序。

    港內點一律排除：公務船的母港（廈門、福州…）天天停著十幾艘，單一鏈結
    分群會把整個港區串成一個橫跨 30km 的假「編隊」。與 analyze_suspicious /
    detect_ship_transfers 共用 geofence 的港口清單。
    """
    snapshots = []
    in_port_skipped = 0
    for entry in track_data or []:
        ts = parse_ts(entry.get('timestamp'))
        if ts is None:
            continue
        govs = []
        for v in entry.get('vessels', []):
            cat = gov_category(v)
            if not cat or not v.get('mmsi'):
                continue
            lat, lon = v.get('lat'), v.get('lon')
            if exclude_ports and lat is not None and lon is not None:
                if geofence.is_in_port_cached(lat, lon):
                    in_port_skipped += 1
                    continue
            govs.append({
                'mmsi': str(v['mmsi']),
                'name': v.get('name') or '',
                'category': cat,
                'lat': v.get('lat'),
                'lon': v.get('lon'),
                'speed': v.get('speed', 0),
            })
        if govs:
            snapshots.append((ts, govs))
    snapshots.sort(key=lambda x: x[0])
    if in_port_skipped:
        print(f"⚓ 港內公務船觀測點排除: {in_port_skipped}")
    return snapshots


def _new_formation(ts, group):
    return {
        'first_seen': ts,
        'last_seen': ts,
        'members': {v['mmsi']: dict(v) for v in group},
        'member_obs': {v['mmsi']: 1 for v in group},
        'observations': 1,
        'max_vessels': len(group),
        'radii': [group_radius_km(group)],
        'speeds': [v.get('speed') or 0 for v in group],
        'centroid_track': [_centroid(ts, group)],
    }


def _centroid(ts, group):
    return {
        't': ts.isoformat(),
        'lat': round(sum(v['lat'] for v in group) / len(group), 5),
        'lon': round(sum(v['lon'] for v in group) / len(group), 5),
        'n': len(group),
    }


def _extend(formation, ts, group):
    formation['last_seen'] = ts
    formation['observations'] += 1
    formation['max_vessels'] = max(formation['max_vessels'], len(group))
    formation['radii'].append(group_radius_km(group))
    formation['speeds'].extend(v.get('speed') or 0 for v in group)
    formation['centroid_track'].append(_centroid(ts, group))
    for v in group:
        # 成員資料以最新一次觀測為準（船名可能中途才廣播出來）
        formation['members'][v['mmsi']] = dict(v)
        formation['member_obs'][v['mmsi']] = \
            formation['member_obs'].get(v['mmsi'], 0) + 1


def track_formations(snapshots,
                     min_duration_hours=FORMATION_MIN_DURATION_HOURS,
                     max_gap_hours=FORMATION_MAX_GAP_HOURS):
    """跨快照串接編隊。

    串接規則：新群與進行中編隊的**共同成員 ≥2 艘**且時間間隔 ≤max_gap_hours
    即視為同一編隊延續。只要 1 艘重疊就串接會把不同編隊經過同一海域時黏成
    一個假事件；要求 2 艘（即編隊的最小構成）才算延續。
    回傳: [formation dict, ...]（已過濾持續時間門檻，依 last_seen 由新到舊）
    """
    open_formations = []
    closed = []

    for ts, govs in snapshots:
        # 逾時未再觀測到的編隊先行結案
        still_open = []
        for f in open_formations:
            if (ts - f['last_seen']).total_seconds() / 3600 > max_gap_hours:
                closed.append(f)
            else:
                still_open.append(f)
        open_formations = still_open

        for group in cluster_by_distance(govs):
            mmsis = {v['mmsi'] for v in group}
            best, best_overlap = None, 0
            for f in open_formations:
                overlap = len(mmsis & set(f['members']))
                if overlap >= FORMATION_MIN_VESSELS and overlap > best_overlap:
                    best, best_overlap = f, overlap
            if best is not None:
                _extend(best, ts, group)
            else:
                open_formations.append(_new_formation(ts, group))

    closed.extend(open_formations)

    results = []
    for f in closed:
        hours = (f['last_seen'] - f['first_seen']).total_seconds() / 3600
        if hours < min_duration_hours:
            continue
        formation = _finalize(f, hours)
        if is_berthed(formation):
            continue
        results.append(formation)
    results.sort(key=lambda x: x['last_seen'], reverse=True)
    return results


def is_berthed(formation):
    """整段期間中心點幾乎沒動、成員速度中位數近乎 0 → 碼頭/錨地，非編隊作業。

    geofence 的港口清單擋不掉沒列入的沿岸基地（例：閩江口的海事局泊地），
    這條通用規則補上；海上低速徘徊的編隊中心點仍會漂移數公里，不會誤殺。
    """
    return (formation['centroid_displacement_km'] < FORMATION_MIN_DISPLACEMENT_KM
            and formation['median_speed_kn'] < FORMATION_BERTH_MAX_KNOTS)


def _finalize(f, duration_hours):
    members = []
    for mmsi, v in f['members'].items():
        members.append({
            'mmsi': mmsi,
            'name': v.get('name', ''),
            'category': v.get('category'),
            'observations': f['member_obs'].get(mmsi, 0),
            'last_lat': v.get('lat'),
            'last_lon': v.get('lon'),
        })
    # 類別排序：海警 → 海巡 → 海救 → 科研，同類別依觀測次數
    order = {c: i for i, c in enumerate(GOV_CATEGORIES)}
    members.sort(key=lambda m: (order.get(m['category'], 9), -m['observations']))

    categories = {}
    for m in members:
        categories[m['category']] = categories.get(m['category'], 0) + 1

    has_research = 'research' in categories
    has_escort = any(c in categories for c in ESCORT_CATEGORIES)
    escorted_research = has_research and has_escort

    track = f['centroid_track']
    if len(track) > MAX_CENTROID_POINTS:
        step = len(track) / MAX_CENTROID_POINTS
        track = [track[int(i * step)] for i in range(MAX_CENTROID_POINTS)]
        track[-1] = f['centroid_track'][-1]
    last = f['centroid_track'][-1]

    # 中心點位移：距起點最遠的中心點（供泊地抑制判定）
    origin = f['centroid_track'][0]
    displacement = max(
        haversine_km(origin['lat'], origin['lon'], c['lat'], c['lon'])
        for c in f['centroid_track'])
    speeds = sorted(f['speeds']) or [0]
    median_speed = speeds[len(speeds) // 2]

    mmsi_key = '-'.join(sorted(f['members'])[:2])
    formation = {
        'id': f"GF-{f['first_seen'].strftime('%Y%m%dT%H%M')}-{mmsi_key}",
        'first_seen': f['first_seen'].isoformat(),
        'last_seen': f['last_seen'].isoformat(),
        'duration_hours': round(duration_hours, 1),
        'observations': f['observations'],
        'vessel_count': len(members),
        'max_vessels': f['max_vessels'],
        'members': members,
        'categories': categories,
        'escorted_research': escorted_research,
        'mean_radius_km': round(sum(f['radii']) / len(f['radii']), 2),
        'max_radius_km': round(max(f['radii']), 2),
        'centroid_displacement_km': round(displacement, 2),
        'median_speed_kn': round(median_speed, 1),
        'centroid_track': track,
        'last_lat': last['lat'],
        'last_lon': last['lon'],
    }
    formation['severity'] = classify_severity(formation)
    return formation


def classify_severity(formation):
    """編隊關注等級。

    護航科考（科研 + 執法船）是本監測最關切的樣態 — 科研船負責測繪/佈放，
    海警負責驅離我方公務船，兩者同框即構成完整的灰色地帶作業單元。
    """
    hours = formation['duration_hours']
    if formation['escorted_research']:
        return 'high' if hours >= 12 else 'medium'
    if formation['max_vessels'] >= 4 or hours >= 48:
        return 'medium'
    return 'low'


def build_mmsi_index(formations):
    """{mmsi: {count, max_duration_hours, escorted_research, severity, ids}}
    — 供 analyze_suspicious.py 計分使用。"""
    idx = {}
    for f in formations:
        for m in f['members']:
            rec = idx.setdefault(m['mmsi'], {
                'count': 0,
                'max_duration_hours': 0.0,
                'escorted_research': False,
                'severity': 'low',
                'formation_ids': [],
            })
            rec['count'] += 1
            rec['max_duration_hours'] = max(rec['max_duration_hours'],
                                            f['duration_hours'])
            rec['escorted_research'] |= f['escorted_research']
            if f['severity'] == 'high' or (f['severity'] == 'medium'
                                           and rec['severity'] == 'low'):
                rec['severity'] = f['severity']
            rec['formation_ids'].append(f['id'])
    return idx


def split_active(formations, now=None, active_hours=FORMATION_ACTIVE_HOURS):
    """依 last_seen 分成「進行中」與「歷史」。"""
    if now is None:
        now = datetime.now(timezone.utc)
    active, history = [], []
    for f in formations:
        last = parse_ts(f['last_seen'])
        f['active'] = bool(
            last and (now - last).total_seconds() / 3600 <= active_hours)
        (active if f['active'] else history).append(f)
    return active, history


def main():
    print("=" * 60)
    print("🛡️ 公務船編隊偵測 — Gov Vessel Formation Detection")
    print("=" * 60)

    if not TRACK_HISTORY_FILE.exists():
        print(f"⚠️ 找不到 {TRACK_HISTORY_FILE}，跳過")
        return

    with open(TRACK_HISTORY_FILE, 'r', encoding='utf-8') as f:
        track_data = json.load(f)

    snapshots = load_gov_snapshots(track_data)
    print(f"📂 tier-1 快照: {len(track_data)} 筆，其中 {len(snapshots)} 筆含公務船")

    formations = track_formations(snapshots)
    active, history = split_active(formations)
    mmsi_index = build_mmsi_index(formations)

    escorted = [f for f in formations if f['escorted_research']]
    output = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'methodology': 'Gov Vessel Formation Detection',
        'criteria': {
            'formation_radius_km': FORMATION_RADIUS_KM,
            'formation_min_vessels': FORMATION_MIN_VESSELS,
            'formation_min_duration_hours': FORMATION_MIN_DURATION_HOURS,
            'formation_max_gap_hours': FORMATION_MAX_GAP_HOURS,
            'formation_active_hours': FORMATION_ACTIVE_HOURS,
            'gov_categories': list(GOV_CATEGORIES),
            'escort_categories': list(ESCORT_CATEGORIES),
        },
        'summary': {
            'total_formations': len(formations),
            'active_formations': len(active),
            'escorted_research': len(escorted),
            'vessels_involved': len(mmsi_index),
            'severity_distribution': _severity_counts(formations),
        },
        'active_formations': active,
        'history': history[:50],
        'vessel_index': mmsi_index,
    }
    atomic_write_json(OUTPUT_FILE, output)

    print(f"\n📋 偵測結果:")
    print(f"   成案編隊 (≥{FORMATION_MIN_DURATION_HOURS}h): {len(formations)}")
    print(f"   進行中: {len(active)}")
    print(f"   護航科考 (科研+執法船): {len(escorted)}")
    print(f"   涉入船隻: {len(mmsi_index)}")
    for f in active[:5]:
        names = ' + '.join(
            f"{m['name'] or m['mmsi']}({m['category']})" for m in f['members'][:4])
        print(f"   • [{f['severity']}] {names} — {f['duration_hours']}h "
              f"@ {f['last_lat']:.3f},{f['last_lon']:.3f}")
    print(f"\n📁 結果已輸出至: {OUTPUT_FILE}")


def _severity_counts(formations):
    counts = {'high': 0, 'medium': 0, 'low': 0}
    for f in formations:
        counts[f['severity']] = counts.get(f['severity'], 0) + 1
    return counts


if __name__ == '__main__':
    main()
