#!/usr/bin/env python3
"""
AISStream.io 資料收集腳本
收集台灣周邊的即時 AIS 船隻資料並儲存為 JSON

使用方式：
    設定環境變數 AISSTREAM_API_KEY
    python fetch_ais_data.py
"""

import os
import json
import asyncio
import websockets
from datetime import datetime, timezone
from collections import defaultdict

# 配置
API_KEY = os.environ.get('AISSTREAM_API_KEY', '')
TAIWAN_BBOX = [[21.0, 117.0], [27.0, 126.0]]  # [緯度, 經度]
COLLECTION_TIME = 180  # 收集 3 分鐘的資料
OUTPUT_FILE = 'data/ais_snapshot.json'

# 軍演區域定義
DRILL_ZONES = {
    'north': {'name': '北區', 'bounds': [[25.5, 121.0], [26.8, 122.5]]},
    'east': {'name': '東區', 'bounds': [[23.0, 122.5], [25.5, 125.0]]},
    'south': {'name': '南區', 'bounds': [[21.5, 119.0], [23.0, 121.0]]},
    'west': {'name': '西區', 'bounds': [[23.5, 118.5], [25.0, 120.0]]}
}

# 電纜路線（簡化座標用於距離計算）
CABLE_ROUTES = [
    {'name': 'Taiwan-Matsu No.4', 'coords': [[25.17, 121.46], [26.16, 120.32], [25.97, 119.94]]},
    {'name': 'TPKM2', 'coords': [[25.05, 121.5], [25.0, 120.5], [24.5, 119.5], [26.1, 119.9]]},
    {'name': 'TPKM3', 'coords': [[25.1, 121.45], [24.95, 120.45], [24.45, 119.45], [26.05, 119.85]]},
    {'name': 'TSE-1', 'coords': [[25.0, 121.5], [25.5, 120.0], [26.0, 119.3]]},
    {'name': 'CSCN', 'coords': [[25.15, 121.55], [25.2, 120.2], [24.45, 118.8]]},
]

# 船隻類型對照
VESSEL_TYPE_MAP = {
    30: 'fishing',
    31: 'towing',
    32: 'towing',
    33: 'dredging',
    34: 'diving',
    35: 'military',
    36: 'sailing',
    37: 'pleasure',
    50: 'pilot',
    51: 'sar',
    52: 'tug',
    53: 'port_tender',
    55: 'law_enforcement',
    60: 'passenger',
    61: 'passenger',
    70: 'cargo',
    71: 'cargo',
    72: 'cargo',
    73: 'cargo',
    74: 'cargo',
    80: 'tanker',
    81: 'tanker',
    82: 'tanker',
    83: 'tanker',
    84: 'tanker',
}


def is_in_zone(lat, lon, bounds):
    """檢查座標是否在指定區域內"""
    return (bounds[0][0] <= lat <= bounds[1][0] and 
            bounds[0][1] <= lon <= bounds[1][1])


def distance_to_cable(lat, lon, cable_coords):
    """計算船隻到電纜的最近距離（簡化計算，單位：度）"""
    min_dist = float('inf')
    for coord in cable_coords:
        dist = ((lat - coord[0])**2 + (lon - coord[1])**2)**0.5
        min_dist = min(min_dist, dist)
    return min_dist


def is_near_cable(lat, lon, threshold=0.3):
    """檢查船隻是否在電纜附近（約30公里）"""
    for cable in CABLE_ROUTES:
        if distance_to_cable(lat, lon, cable['coords']) < threshold:
            return True
    return False


async def collect_ais_data():
    """連接 AISStream 並收集資料"""
    
    if not API_KEY:
        print("⚠️ 未設定 AISSTREAM_API_KEY，使用模擬資料")
        return generate_mock_data()
    
    vessels = {}
    message_count = 0
    start_time = datetime.now(timezone.utc)
    
    print(f"🔗 連接 AISStream.io...")
    print(f"📍 監測區域: {TAIWAN_BBOX}")
    print(f"⏱️ 收集時間: {COLLECTION_TIME} 秒")
    
    try:
        async with websockets.connect('wss://stream.aisstream.io/v0/stream') as ws:
            # 訂閱台灣周邊
            subscribe_msg = {
                'APIKey': API_KEY,
                'BoundingBoxes': [TAIWAN_BBOX],
                'FilterMessageTypes': ['PositionReport', 'ShipStaticData']
            }
            await ws.send(json.dumps(subscribe_msg))
            print("✅ 已訂閱台灣周邊 AIS 資料流")
            
            while (datetime.now(timezone.utc) - start_time).seconds < COLLECTION_TIME:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = json.loads(msg)
                    message_count += 1
                    
                    meta = data.get('MetaData', {})
                    mmsi = str(meta.get('MMSI', ''))
                    lat = meta.get('latitude')
                    lon = meta.get('longitude')
                    
                    if not mmsi or lat is None or lon is None:
                        continue
                    
                    # 更新船隻資料
                    if mmsi not in vessels:
                        vessels[mmsi] = {
                            'mmsi': mmsi,
                            'name': meta.get('ShipName', '').strip() or f'MMSI-{mmsi}',
                            'lat': lat,
                            'lon': lon,
                            'type': 0,
                            'type_name': 'unknown',
                            'speed': 0,
                            'heading': 0,
                            'in_drill_zone': None,
                            'near_cable': False,
                            'last_update': datetime.now(timezone.utc).isoformat()
                        }
                    
                    vessel = vessels[mmsi]
                    vessel['lat'] = lat
                    vessel['lon'] = lon
                    vessel['last_update'] = datetime.now(timezone.utc).isoformat()
                    
                    if meta.get('ShipName'):
                        vessel['name'] = meta['ShipName'].strip()
                    
                    # 處理位置報告
                    if data.get('MessageType') == 'PositionReport':
                        pr = data.get('Message', {}).get('PositionReport', {})
                        vessel['speed'] = pr.get('Sog', 0)
                        vessel['heading'] = pr.get('TrueHeading') or pr.get('Cog', 0)
                    
                    # 處理靜態資料
                    if data.get('MessageType') == 'ShipStaticData':
                        sd = data.get('Message', {}).get('ShipStaticData', {})
                        vessel['type'] = sd.get('Type', 0)
                        vessel['type_name'] = VESSEL_TYPE_MAP.get(vessel['type'], 'other')
                        vessel['destination'] = sd.get('Destination', '')
                    
                    # 檢查是否在軍演區
                    for zone_id, zone in DRILL_ZONES.items():
                        if is_in_zone(lat, lon, zone['bounds']):
                            vessel['in_drill_zone'] = zone_id
                            break
                    else:
                        vessel['in_drill_zone'] = None
                    
                    # 檢查是否在電纜附近
                    vessel['near_cable'] = is_near_cable(lat, lon)
                    
                    # 進度顯示
                    if message_count % 100 == 0:
                        elapsed = (datetime.now(timezone.utc) - start_time).seconds
                        print(f"📥 已收集 {message_count} 訊息, {len(vessels)} 艘船隻 ({elapsed}s / {COLLECTION_TIME}s)")
                
                except asyncio.TimeoutError:
                    continue
                except json.JSONDecodeError:
                    continue
            
            print(f"\n✅ 收集完成!")
            print(f"   總訊息: {message_count}")
            print(f"   船隻數: {len(vessels)}")
    
    except Exception as e:
        print(f"❌ 連接錯誤: {e}")
        return generate_mock_data()
    
    return vessels


def generate_mock_data():
    """生成模擬資料（當無法連接 API 時使用）"""
    import random
    
    print("📦 生成模擬資料...")
    
    vessels = {}
    for i in range(50):
        mmsi = str(100000000 + i)
        lat = random.uniform(22.0, 26.0)
        lon = random.uniform(118.0, 124.0)
        vtype = random.choice([30, 70, 71, 80, 0])
        
        vessel = {
            'mmsi': mmsi,
            'name': f'MOCK-{i:03d}',
            'lat': lat,
            'lon': lon,
            'type': vtype,
            'type_name': VESSEL_TYPE_MAP.get(vtype, 'other'),
            'speed': random.uniform(0, 15),
            'heading': random.uniform(0, 360),
            'in_drill_zone': None,
            'near_cable': is_near_cable(lat, lon),
            'last_update': datetime.now(timezone.utc).isoformat()
        }
        
        # 檢查軍演區
        for zone_id, zone in DRILL_ZONES.items():
            if is_in_zone(lat, lon, zone['bounds']):
                vessel['in_drill_zone'] = zone_id
                break
        
        vessels[mmsi] = vessel
    
    return vessels


def analyze_data(vessels):
    """分析收集到的資料"""
    stats = {
        'total_vessels': len(vessels),
        'by_type': defaultdict(int),
        'in_drill_zones': defaultdict(int),
        'near_cables': 0,
        'fishing_vessels': 0,
        'avg_speed': 0,
    }
    
    total_speed = 0
    for v in vessels.values():
        stats['by_type'][v['type_name']] += 1
        
        if v['in_drill_zone']:
            stats['in_drill_zones'][v['in_drill_zone']] += 1
        
        if v['near_cable']:
            stats['near_cables'] += 1
        
        if v['type_name'] == 'fishing':
            stats['fishing_vessels'] += 1
        
        total_speed += v['speed']
    
    if len(vessels) > 0:
        stats['avg_speed'] = round(total_speed / len(vessels), 2)
    
    # 轉換 defaultdict 為普通 dict
    stats['by_type'] = dict(stats['by_type'])
    stats['in_drill_zones'] = dict(stats['in_drill_zones'])
    
    return stats


def save_data(vessels, stats):
    """儲存資料到 JSON 檔案"""
    
    # 確保目錄存在
    os.makedirs('data', exist_ok=True)
    
    output = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'collection_duration_seconds': COLLECTION_TIME,
        'statistics': stats,
        'drill_zones': {k: v['name'] for k, v in DRILL_ZONES.items()},
        'vessels': list(vessels.values())
    }
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 已儲存至 {OUTPUT_FILE}")
    
    # 同時更新 docs/data.json 供 Dashboard 使用
    dashboard_data = {
        'updated_at': output['updated_at'],
        'ais_data': {
            'vessel_count': stats['total_vessels'],
            'fishing_count': stats['fishing_vessels'],
            'near_cable_count': stats['near_cables'],
            'in_drill_zone_count': sum(stats['in_drill_zones'].values()),
            'drill_zone_breakdown': stats['in_drill_zones'],
            'type_breakdown': stats['by_type'],
            'avg_speed': stats['avg_speed']
        },
        'vessels': list(vessels.values())[:100]  # 只保留前100艘供即時顯示
    }
    
    # 讀取現有 data.json 並合併
    docs_data_file = 'docs/data.json'
    existing_data = {}
    if os.path.exists(docs_data_file):
        with open(docs_data_file, 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
    
    existing_data['updated_at'] = output['updated_at']
    existing_data['ais_snapshot'] = dashboard_data
    
    with open(docs_data_file, 'w', encoding='utf-8') as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)
    
    print(f"💾 已更新 {docs_data_file}")


async def main():
    print("=" * 50)
    print("🛰️ AISStream 台灣周邊船隻資料收集")
    print("=" * 50)
    print(f"時間: {datetime.now(timezone.utc).isoformat()}")
    print()
    
    # 收集資料
    vessels = await collect_ais_data()
    
    # 分析資料
    stats = analyze_data(vessels)
    
    print("\n📊 統計摘要:")
    print(f"   總船隻數: {stats['total_vessels']}")
    print(f"   漁船數量: {stats['fishing_vessels']}")
    print(f"   電纜附近: {stats['near_cables']}")
    print(f"   軍演區內: {sum(stats['in_drill_zones'].values())}")
    print(f"   平均航速: {stats['avg_speed']} kn")
    print(f"   類型分布: {stats['by_type']}")
    
    # 儲存資料
    save_data(vessels, stats)
    
    print("\n✅ 完成!")


if __name__ == '__main__':
    asyncio.run(main())
