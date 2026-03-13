#!/usr/bin/env python3
"""
航港局 AIS 資料收集腳本 - MPB 端點版
功能：從航港局「臺灣海域船舶即時資訊系統」收集 AIS 資料、分析軍演/漁撈熱區、維護歷史紀錄。
資料來源: https://mpbais.motcmpb.gov.tw/aismpb/tools/geojsonais.ashx
"""

import os
import json
import requests
from datetime import datetime, timezone
from collections import defaultdict

# --- 配置區 ---
DATA_DIR = 'data'
DOCS_DIR = 'docs'
OUTPUT_FILE = os.path.join(DATA_DIR, 'ais_snapshot.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'vessel_history.json')
DASHBOARD_FILE = os.path.join(DOCS_DIR, 'data.json')

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


# --- 資料收集 ---

def collect_ais_data():
    """從航港局 MPB 端點取得即時 AIS GeoJSON 資料"""
    print(f"🚀 正在從航港局擷取 AIS 資料...")

    try:
        resp = requests.get(MPB_URL, headers=MPB_HEADERS, timeout=30)
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
        fishing_hotspot = next(
            (hid for hid, h in FISHING_HOTSPOTS.items()
             if is_in_zone(lat, lon, h['bounds'])),
            None
        )

        # 可疑判定已停用，改由 analyze_suspicious.py 綜合分析
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
        'in_fishing_hotspots': {k: 0 for k in FISHING_HOTSPOTS},
    }

    if not vessels:
        stats['by_type'] = {}
        return stats

    total_speed = 0
    for v in vessels.values():
        stats['by_type'][v['type_name']] += 1
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
                history = json.load(f)
        except Exception:
            history = []

    history.append({
        'timestamp': now_str,
        'total_vessels': stats['total_vessels'],
        'fishing_vessels': stats['fishing_vessels'],
        'suspicious_count': stats['suspicious_count'],
        'by_type': stats['by_type'],
    })

    # 保留最近 1000 筆歷史
    history = history[-1000:]
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

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
        'vessels': vessel_list[:100],  # Dashboard 只放前 100 艘
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
