#!/usr/bin/env python3
"""
AISStream.io 資料收集腳本 - 一致性強化版
功能：收集台灣周邊 AIS 資料、分析軍演/漁撈熱區、維護歷史紀錄。
"""

import os
import json
import asyncio
import ssl
import websockets
import websockets.exceptions
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# --- 配置區 ---
API_KEY = os.environ.get('AISSTREAM_API_KEY', '').strip()
TAIWAN_BBOX = [[20, 112], [28, 128]]
COLLECTION_TIME = 180  # 收集 180 秒
DATA_DIR = 'data'
DOCS_DIR = 'docs'
OUTPUT_FILE = os.path.join(DATA_DIR, 'ais_snapshot.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'vessel_history.json')
DASHBOARD_FILE = os.path.join(DOCS_DIR, 'data.json')

# 區域定義 (保持不變)
DRILL_ZONES = {
    'north': {'name': '北區', 'bounds': [[25.5, 121.0], [26.8, 122.5]]},
    'east': {'name': '東區', 'bounds': [[23.0, 122.5], [25.5, 125.0]]},
    'south': {'name': '南區', 'bounds': [[21.5, 119.0], [23.0, 121.0]]},
    'west': {'name': '西區', 'bounds': [[23.5, 118.5], [25.0, 120.0]]}
}

FISHING_HOTSPOTS = {
    'taiwan_bank': {'name': '台灣灘漁場', 'bounds': [[22.0, 117.0], [23.5, 119.5]]},
    'penghu': {'name': '澎湖漁場', 'bounds': [[23.0, 119.0], [24.0, 120.0]]},
    'kuroshio_east': {'name': '東部黑潮漁場', 'bounds': [[22.5, 121.0], [24.5, 122.0]]},
    'northeast': {'name': '東北漁場', 'bounds': [[24.8, 121.5], [25.8, 123.0]]},
    'southwest': {'name': '西南沿岸漁場', 'bounds': [[22.0, 120.0], [23.0, 120.8]]},
}

VESSEL_TYPE_MAP = {30: 'fishing', 35: 'military', 52: 'tug', 60: 'passenger', 70: 'cargo', 80: 'tanker'} # 簡化範例

# --- 工具函式 ---

def is_in_zone(lat, lon, bounds):
    return (bounds[0][0] <= lat <= bounds[1][0] and bounds[0][1] <= lon <= bounds[1][1])

def get_empty_vessel(mmsi):
    """回傳一致的船隻資料結構"""
    return {
        'mmsi': mmsi, 'name': f'MMSI-{mmsi}', 'lat': 0.0, 'lon': 0.0,
        'type': 0, 'type_name': 'unknown', 'speed': 0.0, 'heading': 0.0,
        'in_drill_zone': None, 'in_fishing_hotspot': None, 'suspicious': False,
        'last_update': datetime.now(timezone.utc).isoformat()
    }

async def collect_ais_data():
    if not API_KEY:
        print("⚠️ 無 API KEY")
        return {}

    vessels = {}
    start_time = datetime.now(timezone.utc)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    try:
        async with websockets.connect('wss://stream.aisstream.io/v0/stream', ssl=ssl_context) as ws:
            subscribe_msg = {"APIKey": API_KEY, "BoundingBoxes": [TAIWAN_BBOX]}
            await ws.send(json.dumps(subscribe_msg))

            while (datetime.now(timezone.utc) - start_time).total_seconds() < COLLECTION_TIME:
                try:
                    msg_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = json.loads(msg_raw)
                    
                    meta = data.get('MetaData', {})
                    mmsi = str(meta.get('MMSI', ''))
                    if not mmsi: continue

                    if mmsi not in vessels:
                        vessels[mmsi] = get_empty_vessel(mmsi)
                    
                    v = vessels[mmsi]
                    v['lat'] = meta.get('latitude', v['lat'])
                    v['lon'] = meta.get('longitude', v['lon'])
                    if meta.get('ShipName'): v['name'] = meta['ShipName'].strip()

                    # 處理不同訊息類型
                    msg_type = data.get('MessageType')
                    msg_content = data.get('Message', {})
                    
                    if msg_type == 'PositionReport':
                        pos = msg_content.get('PositionReport', {})
                        v['speed'] = pos.get('Sog', 0.0)
                        v['heading'] = pos.get('TrueHeading', 0.0)
                    elif msg_type == 'ShipStaticData':
                        static = msg_content.get('ShipStaticData', {})
                        v['type'] = static.get('Type', 0)
                        v['type_name'] = VESSEL_TYPE_MAP.get(v['type'], 'other')

                    # 區域判定
                    v['in_drill_zone'] = next((zid for zid, z in DRILL_ZONES.items() if is_in_zone(v['lat'], v['lon'], z['bounds'])), None)
                    v['in_fishing_hotspot'] = next((hid for hid, h in FISHING_HOTSPOTS.items() if is_in_zone(v['lat'], v['lon'], h['bounds'])), None)
                    
                except asyncio.TimeoutError: continue
                except Exception as e: print(f"Loop error: {e}"); break
    except Exception as e:
        print(f"Connection error: {e}")
    
    return vessels

def analyze_data(vessels):
    stats = {
        'total_vessels': len(vessels),
        'fishing_vessels': sum(1 for v in vessels.values() if v['type_name'] == 'fishing'),
        'suspicious_count': 0,
        'avg_speed': 0.0,
        'by_type': defaultdict(int),
        'in_drill_zones': {k: 0 for k in DRILL_ZONES}, # 預設所有區域為 0，確保輸出一致
    }

    if not vessels: return stats

    total_speed = 0
    for v in vessels.values():
        stats['by_type'][v['type_name']] += 1
        if v['in_drill_zone']: stats['in_drill_zones'][v['in_drill_zone']] += 1
        
        # 邏輯：漁船在軍演區但不在漁場 -> 可疑
        if v['type_name'] == 'fishing' and v['in_drill_zone'] and not v['in_fishing_hotspot']:
            v['suspicious'] = True
            stats['suspicious_count'] += 1
        
        total_speed += v['speed']

    stats['avg_speed'] = round(total_speed / len(vessels), 2)
    stats['by_type'] = dict(stats['by_type'])
    return stats

def save_all(vessels, stats):
    """統一儲存入口，確保輸出檔案格式一致"""
    now_str = datetime.now(timezone.utc).isoformat()
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(DOCS_DIR, exist_ok=True)

    # 1. 儲存快照
    full_output = {
        'updated_at': now_str,
        'statistics': stats,
        'vessels': list(vessels.values())
    }
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(full_output, f, ensure_ascii=False, indent=2)

    # 2. 更新 Dashboard 的 ais_snapshot（與 generate_dashboard.py 格式一致）
    existing = {}
    if os.path.exists(DASHBOARD_FILE):
        try:
            with open(DASHBOARD_FILE, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except: pass

    existing['updated_at'] = now_str
    existing['ais_snapshot'] = {
        'updated_at': now_str,
        'ais_data': stats,
        'vessels': list(vessels.values())[:100]
    }

    with open(DASHBOARD_FILE, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

async def main():
    print(f"🚀 開始收集... (預計 {COLLECTION_TIME}s)")
    vessels = await collect_ais_data()
    stats = analyze_data(vessels)
    save_all(vessels, stats)
    print(f"✅ 完成。找到 {len(vessels)} 艘船，可疑: {stats['suspicious_count']}")

if __name__ == '__main__':
    asyncio.run(main())
