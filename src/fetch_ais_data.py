#!/usr/bin/env python3
"""
航港局 AIS 資料收集腳本 - MPB 端點版
功能：從航港局「臺灣海域船舶即時資訊系統」收集 AIS 資料、分析軍演/漁撈熱區、維護歷史紀錄。
資料來源: https://mpbais.motcmpb.gov.tw/aismpb/tools/geojsonais.ashx
"""

import os
import sys
import json
import random
import requests
import urllib3
from datetime import datetime, timezone
from collections import defaultdict

# 航港局 SSL 憑證缺少 Subject Key Identifier，停用驗證
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 配置區 ---
DATA_DIR = 'data'
DOCS_DIR = 'docs'
OUTPUT_FILE = os.path.join(DATA_DIR, 'ais_snapshot.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'vessel_history.json')
VESSEL_PROFILES_FILE = os.path.join(DATA_DIR, 'vessel_profiles.json')
AIS_HISTORY_FILE = os.path.join(DATA_DIR, 'ais_history.json')
AIS_TRACK_FILE = os.path.join(DATA_DIR, 'ais_track_history.json')
DASHBOARD_FILE = os.path.join(DOCS_DIR, 'data.json')

# AIS 歷史快照：每天保留 4 筆（每 6 小時一筆），共保留 90 天 = 360 筆
AIS_HISTORY_MAX_ENTRIES = 360
# AIS 軌跡歷史：保留 14 天，每 2 小時一筆 = 168 筆
AIS_TRACK_MAX_ENTRIES = 168

MPB_URL = "https://mpbais.motcmpb.gov.tw/aismpb/tools/geojsonais.ashx"
MPB_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9,zh;q=0.8,zh-TW;q=0.7",
    "Referer": "https://mpbais.motcmpb.gov.tw/aismpb/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

# 台灣周邊 bounding box (用於過濾非台灣海域資料)
TAIWAN_BBOX = {'lat_min': 20, 'lat_max': 28, 'lon_min': 112, 'lon_max': 128}

# 區域定義
DRILL_ZONES = {
    'north': {'name': '北區', 'bounds': [[25.5, 121.0], [26.8, 122.5]]},
    'east':  {'name': '東區', 'bounds': [[23.0, 122.5], [25.5, 125.0]]},
    'south': {'name': '南區', 'bounds': [[21.5, 119.0], [23.0, 121.0]]},
    'west':  {'name': '西區', 'bounds': [[23.5, 118.5], [25.0, 120.0]]},
}

FISHING_HOTSPOTS = {
    'taiwan_bank':   {'name': '台灣灘漁場',   'bounds': [[22.0, 117.0], [23.5, 119.5]]},
    'penghu':        {'name': '澎湖漁場',     'bounds': [[23.0, 119.0], [24.0, 120.0]]},
    'kuroshio_east': {'name': '東部黑潮漁場', 'bounds': [[22.5, 121.0], [24.5, 122.0]]},
    'northeast':     {'name': '東北漁場',     'bounds': [[24.8, 121.5], [25.8, 123.0]]},
    'southwest':     {'name': '西南沿岸漁場', 'bounds': [[22.0, 120.0], [23.0, 120.8]]},
}

# MPB Ship_and_Cargo_Type 對照表
# AIS 標準: 30-39 漁船, 35 軍事, 50-59 特殊, 60-69 客船, 70-79 貨船, 80-89 油輪
def classify_vessel_type(type_code):
    """根據 AIS Ship_and_Cargo_Type 碼分類船舶"""
    if type_code is None:
        return 'unknown'
    t = int(type_code)
    if 30 <= t <= 39:
        return 'fishing'
    elif t == 35:
        return 'military'
    elif 40 <= t <= 49:
        return 'high_speed'
    elif 50 <= t <= 59:
        return 'special'
    elif 60 <= t <= 69:
        return 'passenger'
    elif 70 <= t <= 79:
        return 'cargo'
    elif 80 <= t <= 89:
        return 'tanker'
    elif t == 0:
        return 'unknown'
    else:
        return 'other'

# --- 工具函式 ---

def is_in_zone(lat, lon, bounds):
    return (bounds[0][0] <= lat <= bounds[1][0] and
            bounds[0][1] <= lon <= bounds[1][1])


def is_in_taiwan_bbox(lat, lon):
    b = TAIWAN_BBOX
    return (b['lat_min'] <= lat <= b['lat_max'] and
            b['lon_min'] <= lon <= b['lon_max'])


# --- SOCKS5 代理設定 ---
# 透過環境變數設定 PingProxies SOCKS5 代理（GitHub Actions 透過 Secrets 傳入）
# 格式: PROXY_LIST = "host:port:user:pass,host:port:user:pass,..."
# 若未設定，則直接連線 MPB（適用本機台灣 IP）

DEFAULT_PROXY_LIST = [
    {"host": "residential.pingproxies.com", "port": 8405, "user": "103521_YHYhJ_c_tw_city_taipei_asn_17421_s_HKU8VCOAGUXIQW1Z", "pass": "47yKTElrP2"},
    {"host": "residential.pingproxies.com", "port": 8176, "user": "103521_YHYhJ_c_tw_city_taipei_asn_17421_s_DIE6KKT8FM98LUQZ", "pass": "47yKTElrP2"},
    {"host": "residential.pingproxies.com", "port": 8714, "user": "103521_YHYhJ_c_tw_city_taipei_asn_17421_s_SO6OXABRJASSOD9F", "pass": "47yKTElrP2"},
    {"host": "residential.pingproxies.com", "port": 8197, "user": "103521_YHYhJ_c_tw_city_taipei_asn_17421_s_46MVSX73Q8S24EBL", "pass": "47yKTElrP2"},
    {"host": "residential.pingproxies.com", "port": 8233, "user": "103521_YHYhJ_c_tw_city_taipei_asn_17421_s_O3L0P2A3ANFWU9L8", "pass": "47yKTElrP2"},
    {"host": "residential.pingproxies.com", "port": 8767, "user": "103521_YHYhJ_c_tw_city_taipei_asn_17421_s_YNI7XFZAJK06VMWY", "pass": "47yKTElrP2"},
    {"host": "residential.pingproxies.com", "port": 8075, "user": "103521_YHYhJ_c_tw_city_taipei_asn_17421_s_MMBJLQNDO7IVE1JG", "pass": "47yKTElrP2"},
    {"host": "residential.pingproxies.com", "port": 8244, "user": "103521_YHYhJ_c_tw_city_taipei_asn_17421_s_WSQOZ7IG16FAQJ81", "pass": "47yKTElrP2"},
    {"host": "residential.pingproxies.com", "port": 8838, "user": "103521_YHYhJ_c_tw_city_taipei_asn_17421_s_NVKQHVJI3S1NG7ID", "pass": "47yKTElrP2"},
    {"host": "residential.pingproxies.com", "port": 8419, "user": "103521_YHYhJ_c_tw_city_taipei_asn_17421_s_ODOYW5ZMW6Z1UMVF", "pass": "47yKTElrP2"},
]


def get_proxy_list():
    """從環境變數或預設清單取得 SOCKS5 代理列表"""
    env_val = os.environ.get('PROXY_LIST', '').strip()
    if env_val:
        proxies = []
        for entry in env_val.split(','):
            parts = entry.strip().split(':')
            if len(parts) == 4:
                proxies.append({
                    'host': parts[0], 'port': int(parts[1]),
                    'user': parts[2], 'pass': parts[3],
                })
        if proxies:
            return proxies
    return DEFAULT_PROXY_LIST


# --- 資料收集 ---

def collect_ais_data():
    """從航港局 MPB 端點取得即時 AIS GeoJSON 資料（支援 SOCKS5 代理）"""
    use_proxy = os.environ.get('USE_PROXY', '').lower() in ('1', 'true', 'yes')

    if use_proxy:
        print(f"🚀 正在透過 SOCKS5 代理從航港局擷取 AIS 資料...")
        proxy_list = get_proxy_list()
        random.shuffle(proxy_list)
        geojson = None
        last_error = None

        for attempt, p in enumerate(proxy_list[:5]):
            proxy_url = f"socks5://{p['user']}:{p['pass']}@{p['host']}:{p['port']}"
            proxies = {"http": proxy_url, "https": proxy_url}
            try:
                print(f"  🔄 嘗試代理 #{attempt+1} (port {p['port']})...")
                resp = requests.get(MPB_URL, headers=MPB_HEADERS, proxies=proxies,
                                    timeout=60, verify=False)
                resp.raise_for_status()
                geojson = resp.json()
                print(f"  ✅ 代理連線成功 (port {p['port']})")
                last_error = None
                break
            except requests.RequestException as e:
                last_error = str(e)
                print(f"  ❌ 代理 #{attempt+1} 失敗: {last_error}")
                continue

        if last_error or geojson is None:
            print(f"❌ 所有代理皆失敗")
            return {}
    else:
        print(f"🚀 正在直接從航港局擷取 AIS 資料（本機模式）...")
        try:
            resp = requests.get(MPB_URL, headers=MPB_HEADERS, timeout=30, verify=False)
            resp.raise_for_status()
            geojson = resp.json()
        except requests.RequestException as e:
            print(f"❌ 請求失敗: {e}")
            return {}

    features = geojson.get("features", [])
    print(f"  HTTP {resp.status_code} | {len(resp.content):,} bytes | {len(features)} features")

    vessels = {}
    skipped = 0

    for feat in features:
        props = feat.get("properties", {})
        coords = feat.get("geometry", {}).get("coordinates", [None, None])

        lon = coords[0] if coords and len(coords) > 0 else None
        lat = coords[1] if coords and len(coords) > 1 else None

        if lon is None or lat is None:
            skipped += 1
            continue

        # 過濾超出台灣海域範圍的資料
        if not is_in_taiwan_bbox(lat, lon):
            skipped += 1
            continue

        mmsi = str(props.get("MMSI", "")).strip()
        if not mmsi or mmsi == "0":
            skipped += 1
            continue

        ship_name = str(props.get("ShipName", "")).strip()
        type_code = props.get("Ship_and_Cargo_Type")
        type_name = classify_vessel_type(type_code)
        sog = props.get("SOG", 0.0) or 0.0
        cog = props.get("COG", 0.0) or 0.0
        record_time = props.get("Record_Time", "")

        # 區域判定
        drill_zone = next(
            (zid for zid, z in DRILL_ZONES.items()
             if is_in_zone(lat, lon, z['bounds'])),
            None
        )
        fishing_hotspot = next(
            (hid for hid, h in FISHING_HOTSPOTS.items()
             if is_in_zone(lat, lon, h['bounds'])),
            None
        )

        # 可疑判定：漁船在軍演區但不在漁場
        suspicious = (type_name == 'fishing' and
                      drill_zone is not None and
                      fishing_hotspot is None)

        vessels[mmsi] = {
            'mmsi': mmsi,
            'name': ship_name if ship_name else f'MMSI-{mmsi}',
            'imo': str(props.get("IMO_Number", "")).strip(),
            'call_sign': str(props.get("Call_Sign", "")).strip(),
            'lat': lat,
            'lon': lon,
            'type': type_code,
            'type_name': type_name,
            'speed': float(sog),
            'heading': float(cog),
            'nav_status': str(props.get("Navigational_Status", "")),
            'in_drill_zone': drill_zone,
            'in_fishing_hotspot': fishing_hotspot,
            'suspicious': suspicious,
            'record_time': record_time,
            'last_update': datetime.now(timezone.utc).isoformat(),
        }

    print(f"  ✅ 有效船舶: {len(vessels)} | 跳過: {skipped}")
    return vessels


# --- 分析 ---

def analyze_data(vessels):
    stats = {
        'total_vessels': len(vessels),
        'fishing_vessels': sum(1 for v in vessels.values() if v['type_name'] == 'fishing'),
        'suspicious_count': 0,
        'avg_speed': 0.0,
        'by_type': defaultdict(int),
        'in_drill_zones': {k: 0 for k in DRILL_ZONES},
        'in_fishing_hotspots': {k: 0 for k in FISHING_HOTSPOTS},
    }

    if not vessels:
        stats['by_type'] = {}
        return stats

    total_speed = 0
    for v in vessels.values():
        stats['by_type'][v['type_name']] += 1
        if v['in_drill_zone']:
            stats['in_drill_zones'][v['in_drill_zone']] += 1
        if v['in_fishing_hotspot']:
            stats['in_fishing_hotspots'][v['in_fishing_hotspot']] += 1
        if v['suspicious']:
            stats['suspicious_count'] += 1
        total_speed += v['speed']

    stats['avg_speed'] = round(total_speed / len(vessels), 2)
    stats['by_type'] = dict(stats['by_type'])
    return stats


# --- 儲存 ---

def save_all(vessels, stats):
    """統一儲存入口，確保輸出檔案格式一致"""
    now_str = datetime.now(timezone.utc).isoformat()
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(DOCS_DIR, exist_ok=True)

    vessel_list = list(vessels.values())

    # API 回傳 0 艘時保留舊快照，避免清空有效資料
    # （常見於 GitHub Actions：MPB API 封鎖境外 IP）
    if not vessel_list:
        print(f"  ⚠️ 本次取得 0 艘船，保留既有快照，跳過覆寫")
        return

    # 1. 儲存快照
    full_output = {
        'updated_at': now_str,
        'source': 'MPB_geojsonais',
        'statistics': stats,
        'vessels': vessel_list,
    }
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(full_output, f, ensure_ascii=False, indent=2)
    print(f"  📄 快照已儲存: {OUTPUT_FILE} ({len(vessel_list)} 艘)")

    # 2. 更新歷史紀錄 (追加每日摘要)
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            history = loaded if isinstance(loaded, list) else []
        except Exception:
            history = []

    history.append({
        'timestamp': now_str,
        'total_vessels': stats['total_vessels'],
        'fishing_vessels': stats['fishing_vessels'],
        'suspicious_count': stats['suspicious_count'],
        'by_type': stats['by_type'],
        'in_drill_zones': stats['in_drill_zones'],
    })

    # 保留最近 1000 筆歷史
    history = history[-1000:]
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    # 2a. 更新船隻行為 profile（供 analyze_suspicious.py CSIS 分析使用）
    profiles = {}
    if os.path.exists(VESSEL_PROFILES_FILE):
        try:
            with open(VESSEL_PROFILES_FILE, 'r', encoding='utf-8') as f:
                profiles = json.load(f)
            if not isinstance(profiles, dict):
                profiles = {}
        except Exception:
            profiles = {}

    for v in vessel_list:
        mmsi = v['mmsi']
        if mmsi not in profiles:
            profiles[mmsi] = {
                'mmsi': mmsi,
                'names_seen': [],
                'types_seen': [],
                'total_snapshots': 0,
                'drill_zone_snapshots': 0,
                'fishing_hotspot_snapshots': 0,
                'last_seen_timestamps': [],
            }

        p = profiles[mmsi]
        p['total_snapshots'] += 1

        # 記錄不同船名
        if v['name'] and v['name'] not in p['names_seen']:
            p['names_seen'].append(v['name'])
        # 記錄不同船型
        if v['type_name'] and v['type_name'] not in p['types_seen']:
            p['types_seen'].append(v['type_name'])
        # 軍演區快照計數
        if v.get('in_drill_zone'):
            p['drill_zone_snapshots'] += 1
        # 漁撈熱點快照計數
        if v.get('in_fishing_hotspot'):
            p['fishing_hotspot_snapshots'] += 1
        # 最近出現時間（保留最近 50 筆，用於 going dark 偵測）
        p['last_seen_timestamps'].append(now_str)
        p['last_seen_timestamps'] = p['last_seen_timestamps'][-50:]

    with open(VESSEL_PROFILES_FILE, 'w', encoding='utf-8') as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)
    print(f"  👤 船隻 profile 已更新: {VESSEL_PROFILES_FILE} ({len(profiles)} 艘)")

    # 2b. AIS 歷史快照（每天 4 筆，每 6 小時一筆，供前端趨勢圖使用）
    now = datetime.now(timezone.utc)
    today_str = now.strftime('%Y-%m-%d')
    period = (now.hour // 6) * 6  # 0, 6, 12, 18
    period_key = f"{today_str}_{period:02d}"

    suspicious_vessels = [
        {
            'mmsi': v['mmsi'],
            'name': v['name'],
            'lat': v['lat'],
            'lon': v['lon'],
            'type_name': v['type_name'],
            'speed': v['speed'],
            'in_drill_zone': v['in_drill_zone'],
        }
        for v in vessel_list if v.get('suspicious')
    ]

    period_snapshot = {
        'date': today_str,
        'period': period,
        'period_key': period_key,
        'timestamp': now_str,
        'stats': {
            'total_vessels': stats['total_vessels'],
            'fishing_vessels': stats['fishing_vessels'],
            'suspicious_count': stats['suspicious_count'],
            'avg_speed': stats['avg_speed'],
            'by_type': stats['by_type'],
            'in_drill_zones': stats['in_drill_zones'],
            'in_fishing_hotspots': stats.get('in_fishing_hotspots', {}),
        },
        'suspicious_vessels': suspicious_vessels,
    }

    ais_history = []
    if os.path.exists(AIS_HISTORY_FILE):
        try:
            with open(AIS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                ais_history = json.load(f)
            if not isinstance(ais_history, list):
                ais_history = []
        except Exception:
            ais_history = []

    # 同一時段（date + period）多次執行只保留最新一筆
    ais_history = [s for s in ais_history if s.get('period_key', s.get('date')) != period_key]
    ais_history.append(period_snapshot)

    # 保留最近 360 筆（90 天 × 4 筆/天）
    ais_history = ais_history[-AIS_HISTORY_MAX_ENTRIES:]
    with open(AIS_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(ais_history, f, ensure_ascii=False, indent=2)
    print(f"  📅 歷史快照已更新: {AIS_HISTORY_FILE} ({len(ais_history)} 筆, period={period:02d}h)")

    # 2c. AIS 軌跡歷史（記錄可疑船 + 軍演區船隻位置，供動畫播放）
    track_vessels = [
        {
            'mmsi': v['mmsi'],
            'name': v['name'],
            'lat': v['lat'],
            'lon': v['lon'],
            'speed': v['speed'],
            'heading': v['heading'],
            'type_name': v['type_name'],
            'in_drill_zone': v['in_drill_zone'],
            'suspicious': v.get('suspicious', False),
        }
        for v in vessel_list
        if v.get('suspicious') or v.get('in_drill_zone')
    ]

    track_entry = {
        'timestamp': now_str,
        'period_key': period_key,
        'vessel_count': len(track_vessels),
        'suspicious_count': sum(1 for v in track_vessels if v.get('suspicious')),
        'vessels': track_vessels,
    }

    track_history = []
    if os.path.exists(AIS_TRACK_FILE):
        try:
            with open(AIS_TRACK_FILE, 'r', encoding='utf-8') as f:
                track_history = json.load(f)
            if not isinstance(track_history, list):
                track_history = []
        except Exception:
            track_history = []

    track_history.append(track_entry)
    track_history = track_history[-AIS_TRACK_MAX_ENTRIES:]
    with open(AIS_TRACK_FILE, 'w', encoding='utf-8') as f:
        json.dump(track_history, f, ensure_ascii=False, indent=2)
    print(f"  🎬 軌跡歷史已更新: {AIS_TRACK_FILE} ({len(track_history)} 筆, {len(track_vessels)} 艘船)")

    # 3. 更新 Dashboard 資料（與 generate_dashboard.py 格式一致）
    existing = {}
    if os.path.exists(DASHBOARD_FILE):
        try:
            with open(DASHBOARD_FILE, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            pass

    existing['updated_at'] = now_str
    existing['ais_snapshot'] = {
        'updated_at': now_str,
        'source': 'MPB_geojsonais',
        'ais_data': stats,
        'vessels': vessel_list,
    }

    with open(DASHBOARD_FILE, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"  📊 Dashboard 已更新: {DASHBOARD_FILE}")


# --- 主程式 ---

def main():
    print(f"{'='*50}")
    print(f"  航港局 AIS 船位收集 (MPB 端點)")
    print(f"  {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    print(f"{'='*50}\n")

    vessels = collect_ais_data()
    stats = analyze_data(vessels)
    save_all(vessels, stats)

    print(f"\n{'='*50}")
    print(f"  ✅ 完成")
    print(f"  船舶總數: {stats['total_vessels']}")
    print(f"  漁船: {stats['fishing_vessels']}")
    print(f"  可疑: {stats['suspicious_count']}")
    print(f"  平均航速: {stats['avg_speed']} kn")
    print(f"  類型分布: {stats['by_type']}")
    print(f"{'='*50}")


if __name__ == '__main__':
    main()

