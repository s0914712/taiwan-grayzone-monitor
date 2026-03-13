#!/usr/bin/env python3
"""
航港局 AIS 資料收集腳本 - MPB 端點版
功能：從航港局「臺灣海域船舶即時資訊系統」收集 AIS 資料、分析軍演/漁撈熱區、維護歷史紀錄。
資料來源: https://mpbais.motcmpb.gov.tw/aismpb/tools/geojsonais.ashx
"""

import os
import re
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
IDENTITY_EVENTS_FILE = os.path.join(DATA_DIR, 'identity_events.json')

# 身分變更事件：保留最近 5000 筆
IDENTITY_EVENTS_MAX = 5000

# AIS 歷史快照：每天保留 12 筆（每 2 小時一筆），共保留 90 天 = 1080 筆
AIS_HISTORY_MAX_ENTRIES = 1080
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
TAIWAN_BBOX = {'lat_min': 19, 'lat_max': 30, 'lon_min': 116, 'lon_max': 130}

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


# --- 大陸漁船識別 ---

_CN_PROVINCE_PATTERNS = [
    re.compile(r'^MIN', re.I),    # 福建 (閩)
    re.compile(r'^闽', re.I),
    re.compile(r'^閩', re.I),
    re.compile(r'^ZHE', re.I),    # 浙江
    re.compile(r'^浙', re.I),
    re.compile(r'^YUE', re.I),    # 廣東 (粵)
    re.compile(r'^粤', re.I),
    re.compile(r'^粵', re.I),
    re.compile(r'^LU\s?YU', re.I),  # 山東 (魯)
    re.compile(r'^鲁', re.I),
    re.compile(r'^魯', re.I),
    re.compile(r'^QIONG', re.I),  # 海南 (瓊)
    re.compile(r'^琼', re.I),
    re.compile(r'^瓊', re.I),
    re.compile(r'^SU\s?YU', re.I),  # 江蘇
    re.compile(r'^苏', re.I),
    re.compile(r'^蘇', re.I),
    re.compile(r'^GUI', re.I),    # 廣西 (桂)
    re.compile(r'^桂', re.I),
    re.compile(r'^XIANG', re.I),  # 湖南 (湘)
    re.compile(r'^湘', re.I),
    re.compile(r'^JIN\s?YU', re.I),  # 天津 (津)
    re.compile(r'^津', re.I),
    re.compile(r'^LIAO', re.I),   # 遼寧
    re.compile(r'^辽', re.I),
    re.compile(r'^遼', re.I),
]
_CN_YU_PATTERN = re.compile(r'YU[.\s]*\d', re.I)


def is_cn_fishing_vessel(name):
    """判斷船名是否符合大陸漁船命名模式"""
    if not name:
        return False
    n = name.upper()
    for pat in _CN_PROVINCE_PATTERNS:
        if pat.search(n):
            return True
    if _CN_YU_PATTERN.search(n):
        return True
    return False


# --- SOCKS5 代理設定 ---
# 優先從 Pool.txt 讀取代理清單（100 個 ASN 3462 HiNet 代理）
# 也支援環境變數 PROXY_LIST 覆蓋（格式: host:port:user:pass,host:port:user:pass,...）
# 若都無法讀取，使用內建備用清單

POOL_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'Pool.txt')

# 嘗試連線的代理數量上限
MAX_PROXY_ATTEMPTS = 10


def _parse_proxy_line(line):
    """解析 host:port:user:pass 格式的代理行"""
    parts = line.strip().split(':')
    if len(parts) == 4:
        return {'host': parts[0], 'port': int(parts[1]), 'user': parts[2], 'pass': parts[3]}
    return None


def get_proxy_list():
    """從 Pool.txt / 環境變數 / 備用清單取得 SOCKS5 代理列表"""
    # 1. 環境變數覆蓋（最高優先）
    env_val = os.environ.get('PROXY_LIST', '').strip()
    if env_val:
        proxies = [_parse_proxy_line(e) for e in env_val.split(',')]
        proxies = [p for p in proxies if p]
        if proxies:
            print(f"  📋 使用環境變數代理清單 ({len(proxies)} 個)")
            return proxies

    # 2. 從 Pool.txt 讀取
    if os.path.exists(POOL_FILE):
        try:
            with open(POOL_FILE, 'r') as f:
                lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
            proxies = [_parse_proxy_line(l) for l in lines]
            proxies = [p for p in proxies if p]
            if proxies:
                print(f"  📋 從 Pool.txt 載入代理清單 ({len(proxies)} 個)")
                return proxies
        except Exception as e:
            print(f"  ⚠️ 讀取 Pool.txt 失敗: {e}")

    # 3. 備用清單
    print(f"  ⚠️ Pool.txt 不存在或為空，使用備用代理清單")
    return [
        {"host": "residential.pingproxies.com", "port": 8253, "user": "103521_YHYhJ_c_tw_city_taipei_asn_3462_m_size_s_82K1Q977SLB9H76Q", "pass": "47yKTElrP2"},
        {"host": "residential.pingproxies.com", "port": 8901, "user": "103521_YHYhJ_c_tw_city_taipei_asn_3462_m_size_s_AZ1PX916DV90HJFS", "pass": "47yKTElrP2"},
        {"host": "residential.pingproxies.com", "port": 8353, "user": "103521_YHYhJ_c_tw_city_taipei_asn_3462_m_size_s_WZ0Q1FVPVDFZF9G8", "pass": "47yKTElrP2"},
        {"host": "residential.pingproxies.com", "port": 8970, "user": "103521_YHYhJ_c_tw_city_taipei_asn_3462_m_size_s_Y60AXR2HBJNRXGU9", "pass": "47yKTElrP2"},
        {"host": "residential.pingproxies.com", "port": 8999, "user": "103521_YHYhJ_c_tw_city_taipei_asn_3462_m_size_s_DGCW7X66DDUGQDZR", "pass": "47yKTElrP2"},
    ]


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

        for attempt, p in enumerate(proxy_list[:MAX_PROXY_ATTEMPTS]):
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

        # 可疑判定已停用（原 CSIS 方法論），改由 analyze_suspicious.py 綜合分析
        suspicious = False

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


# --- 身分變更偵測 ---

def detect_identity_changes(vessels, profiles):
    """比對當前船舶資料與歷史 profile，偵測船名/呼號/IMO 變更"""
    events = []
    now_str = datetime.now(timezone.utc).isoformat()

    for v in vessels.values():
        mmsi = v['mmsi']
        if mmsi not in profiles:
            continue
        p = profiles[mmsi]
        changes = []

        # 比對船名
        old_name = p.get('last_name', '')
        new_name = v.get('name', '')
        if old_name and new_name and old_name != new_name:
            changes.append({'field': 'name', 'old': old_name, 'new': new_name})

        # 比對呼號
        old_cs = p.get('last_call_sign', '')
        new_cs = v.get('call_sign', '')
        if old_cs and new_cs and old_cs != new_cs:
            changes.append({'field': 'call_sign', 'old': old_cs, 'new': new_cs})

        # 比對 IMO
        old_imo = p.get('last_imo', '')
        new_imo = v.get('imo', '')
        if old_imo and new_imo and old_imo != new_imo:
            changes.append({'field': 'imo', 'old': old_imo, 'new': new_imo})

        if changes:
            events.append({
                'mmsi': mmsi,
                'timestamp': now_str,
                'changes': changes,
                'multi_field': len(changes) > 1,
                'lat': v['lat'],
                'lon': v['lon'],
                'in_drill_zone': v.get('in_drill_zone'),
                'in_fishing_hotspot': v.get('in_fishing_hotspot'),
            })

    return events


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

    # 2a-1. 偵測身分變更（必須在更新 profile 之前）
    new_events = detect_identity_changes(vessels, profiles)
    if new_events:
        # 載入既有事件，追加新事件，保留上限
        id_events = []
        if os.path.exists(IDENTITY_EVENTS_FILE):
            try:
                with open(IDENTITY_EVENTS_FILE, 'r', encoding='utf-8') as f:
                    id_events = json.load(f)
                if not isinstance(id_events, list):
                    id_events = []
            except Exception:
                id_events = []
        id_events.extend(new_events)
        id_events = id_events[-IDENTITY_EVENTS_MAX:]
        with open(IDENTITY_EVENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(id_events, f, ensure_ascii=False, indent=2)
        print(f"  🔄 偵測到 {len(new_events)} 筆身分變更事件 (累計 {len(id_events)} 筆)")

    # 2a-2. 更新 profile（含 last_* 追蹤欄位）
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

        # 更新 last_* 欄位（供下次身分變更比對）
        if v.get('name'):
            p['last_name'] = v['name']
        if v.get('call_sign'):
            p['last_call_sign'] = v['call_sign']
        if v.get('imo'):
            p['last_imo'] = v['imo']

    with open(VESSEL_PROFILES_FILE, 'w', encoding='utf-8') as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)
    print(f"  👤 船隻 profile 已更新: {VESSEL_PROFILES_FILE} ({len(profiles)} 艘)")

    # 2b. AIS 歷史快照（每天 12 筆，每 2 小時一筆，供前端趨勢圖使用）
    now = datetime.now(timezone.utc)
    today_str = now.strftime('%Y-%m-%d')
    period = (now.hour // 2) * 2  # 0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22
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

    # 2c. AIS 軌跡歷史（記錄大陸漁船 + 可疑船位置，供動畫播放）
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
        if is_cn_fishing_vessel(v.get('name')) or v.get('suspicious')
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

