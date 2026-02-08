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
import ssl
import websockets
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# 配置
API_KEY = os.environ.get('AISSTREAM_API_KEY', '').strip()
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

# 漁撈熱點定義（台灣周邊已知高產漁場）
# 參考：漁業署公開漁場資料、GFW fishing effort 熱區
FISHING_HOTSPOTS = {
    'taiwan_bank': {
        'name': '台灣灘漁場',
        'bounds': [[22.0, 117.0], [23.5, 119.5]],
        'description': '台灣海峽西南部淺灘，底拖網主要漁場'
    },
    'penghu': {
        'name': '澎湖漁場',
        'bounds': [[23.0, 119.0], [24.0, 120.0]],
        'description': '澎湖群島周邊，刺網與延繩釣漁場'
    },
    'kuroshio_east': {
        'name': '東部黑潮漁場',
        'bounds': [[22.5, 121.0], [24.5, 122.0]],
        'description': '黑潮流經台灣東部，鮪魚延繩釣漁場'
    },
    'northeast': {
        'name': '東北漁場',
        'bounds': [[24.8, 121.5], [25.8, 123.0]],
        'description': '基隆-宜蘭外海，鎖管棒受網漁場'
    },
    'southwest': {
        'name': '西南沿岸漁場',
        'bounds': [[22.0, 120.0], [23.0, 120.8]],
        'description': '東港-小琉球，近海拖網漁場'
    },
}

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


def get_fishing_hotspot(lat, lon):
    """檢查船隻是否在漁撈熱點內，回傳熱點 ID 或 None"""
    for hotspot_id, hotspot in FISHING_HOTSPOTS.items():
        if is_in_zone(lat, lon, hotspot['bounds']):
            return hotspot_id
    return None


async def collect_ais_data():
    """連接 AISStream 並收集資料
    參考官方範例: https://github.com/aisstream/example/tree/main/python
    """

    if not API_KEY:
        print("⚠️ 未設定 AISSTREAM_API_KEY，跳過 AIS 資料收集")
        return {}

    vessels = {}
    message_count = 0
    error_count = 0
    start_time = datetime.now(timezone.utc)

    print(f"🔗 連接 AISStream.io...")
    print(f"🔑 API Key: {API_KEY[:8]}...{API_KEY[-4:]}" if len(API_KEY) > 12 else f"🔑 API Key: (length={len(API_KEY)})")
    print(f"📍 監測區域: {TAIWAN_BBOX}")
    print(f"⏱️ 收集時間: {COLLECTION_TIME} 秒")

    def process_message(data):
        """處理單一 AIS 訊息"""
        meta = data.get('MetaData', {})
        mmsi = str(meta.get('MMSI', ''))
        lat = meta.get('latitude')
        lon = meta.get('longitude')

        if not mmsi or lat is None or lon is None:
            return

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
                'in_fishing_hotspot': None,
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

        # 檢查是否在漁撈熱點
        vessel['in_fishing_hotspot'] = get_fishing_hotspot(lat, lon)

    # SSL 設定（參考官方 main_ssl_disabled.py，GitHub Actions 環境可能有 SSL 驗證問題）
    ssl_ctx = ssl.SSLContext()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    # 嘗試兩種連線方式：先用 SSL 禁用模式，失敗再用預設模式
    for attempt, use_ssl_ctx in enumerate([(ssl_ctx, "SSL-relaxed"), (None, "SSL-default")], 1):
        ssl_opt, ssl_label = use_ssl_ctx
        try:
            print(f"\n🔗 連線嘗試 #{attempt} ({ssl_label})...")
            ws_kwargs = {
                'ping_interval': 20,
                'ping_timeout': 20,
                'close_timeout': 10,
            }
            if ssl_opt:
                ws_kwargs['ssl'] = ssl_opt

            async with websockets.connect(
                'wss://stream.aisstream.io/v0/stream',
                **ws_kwargs
            ) as ws:
                print(f"   ✅ WebSocket 連線成功 ({ssl_label})")

                # 訂閱台灣周邊（必須在連線 3 秒內送出）
                subscribe_msg = {
                    "APIKey": API_KEY,
                    "BoundingBoxes": [TAIWAN_BBOX]
                }
                await ws.send(json.dumps(subscribe_msg))
                print("   ✅ 已送出訂閱請求")

                # 嘗試接收第一則訊息確認連線有效
                try:
                    first_msg = await asyncio.wait_for(ws.recv(), timeout=15)
                    first_data = json.loads(first_msg)
                    message_count += 1

                    msg_type = first_data.get('MessageType', 'unknown')
                    print(f"   📨 首則訊息: type={msg_type}, keys={list(first_data.keys())}")

                    if 'error' in first_data or 'Error' in first_data:
                        err_msg = first_data.get('error') or first_data.get('Error', '')
                        print(f"   ❌ API 錯誤回應: {err_msg}")
                        return {}

                    process_message(first_data)
                except asyncio.TimeoutError:
                    print("   ⚠️ 15 秒內未收到任何訊息，連線可能有問題")
                    # 仍然繼續等待

                # 收集迴圈
                async def collect_loop():
                    nonlocal message_count, error_count
                    async for msg_raw in ws:
                        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                        if elapsed >= COLLECTION_TIME:
                            break

                        try:
                            data = json.loads(msg_raw)
                        except json.JSONDecodeError:
                            error_count += 1
                            if error_count <= 3:
                                print(f"   ⚠️ JSON 解析失敗: {str(msg_raw)[:200]}")
                            continue

                        message_count += 1

                        if message_count <= 3:
                            msg_type = data.get('MessageType', 'unknown')
                            print(f"   📨 訊息 #{message_count}: type={msg_type}")

                        if 'error' in data or 'Error' in data:
                            err_msg = data.get('error') or data.get('Error', '')
                            print(f"   ❌ API 錯誤: {err_msg}")
                            continue

                        process_message(data)

                        if message_count % 100 == 0:
                            print(f"📥 已收集 {message_count} 訊息, {len(vessels)} 艘船隻 ({elapsed:.0f}s)")

                try:
                    await asyncio.wait_for(collect_loop(), timeout=COLLECTION_TIME + 30)
                except asyncio.TimeoutError:
                    print("⏰ 收集超時")

                print(f"\n✅ 收集完成!")
                print(f"   總訊息: {message_count}")
                print(f"   解析錯誤: {error_count}")
                print(f"   船隻數: {len(vessels)}")

                if message_count == 0:
                    print("   ⚠️ 未收到任何訊息！可能原因：")
                    print("      1. API Key 無效或已過期")
                    print("      2. AISStream 服務暫時不可用")
                    print("      3. 請至 https://aisstream.io 確認 API Key 狀態")

                # 連線成功，不需要嘗試第二種方式
                break

        except websockets.exceptions.InvalidStatusCode as e:
            print(f"   ❌ WebSocket 被拒絕: HTTP {e.status_code}")
            if e.status_code == 403:
                print("   → API Key 無效，請至 aisstream.io 確認")
                return {}
            if attempt < 2:
                print("   → 嘗試下一種連線方式...")
                continue
            return {}
        except websockets.exceptions.ConnectionClosedError as e:
            print(f"   ❌ 連線被關閉: {e}")
            if attempt < 2:
                print("   → 嘗試下一種連線方式...")
                continue
            print("   → 兩種連線方式都失敗")
            print("   → 請確認 API Key 是否有效: https://aisstream.io")
            return {}
        except Exception as e:
            print(f"   ❌ 連接錯誤: {type(e).__name__}: {e}")
            if attempt < 2:
                print("   → 嘗試下一種連線方式...")
                continue
            return {}

    return vessels


def analyze_data(vessels):
    """分析收集到的資料"""
    stats = {
        'total_vessels': len(vessels),
        'by_type': defaultdict(int),
        'in_drill_zones': defaultdict(int),
        'in_fishing_hotspots': defaultdict(int),
        'fishing_vessels': 0,
        'suspicious_count': 0,
        'avg_speed': 0,
    }

    total_speed = 0
    for v in vessels.values():
        stats['by_type'][v['type_name']] += 1

        if v['in_drill_zone']:
            stats['in_drill_zones'][v['in_drill_zone']] += 1

        if v.get('in_fishing_hotspot'):
            stats['in_fishing_hotspots'][v['in_fishing_hotspot']] += 1

        if v['type_name'] == 'fishing':
            stats['fishing_vessels'] += 1

        # 即時可疑判定：漁船在軍演區但不在漁撈熱點
        if (v['type_name'] == 'fishing' and
                v['in_drill_zone'] and
                not v.get('in_fishing_hotspot')):
            v['suspicious'] = True
            stats['suspicious_count'] += 1
        else:
            v['suspicious'] = False

        total_speed += v['speed']

    if len(vessels) > 0:
        stats['avg_speed'] = round(total_speed / len(vessels), 2)

    # 轉換 defaultdict 為普通 dict
    stats['by_type'] = dict(stats['by_type'])
    stats['in_drill_zones'] = dict(stats['in_drill_zones'])
    stats['in_fishing_hotspots'] = dict(stats['in_fishing_hotspots'])

    return stats


def update_vessel_history(vessels):
    """累積船隻歷史觀測記錄，用於 CSIS 行為分析"""
    os.makedirs('data', exist_ok=True)
    history_file = 'data/vessel_history.json'

    # 載入既有歷史
    history = {}
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            history = {}

    now = datetime.now(timezone.utc).isoformat()

    for v in vessels.values():
        mmsi = v['mmsi']
        if mmsi not in history:
            history[mmsi] = {
                'mmsi': mmsi,
                'names_seen': [],
                'types_seen': [],
                'total_snapshots': 0,
                'drill_zone_snapshots': 0,
                'fishing_hotspot_snapshots': 0,
                'first_seen': now,
                'last_seen': now,
                'snapshots': [],
            }

        profile = history[mmsi]
        profile['total_snapshots'] += 1
        profile['last_seen'] = now

        # 追蹤船名變更（AIS 異常指標）
        name = v.get('name', '')
        if name and name not in profile['names_seen']:
            profile['names_seen'].append(name)

        # 追蹤船型變更
        type_name = v.get('type_name', 'unknown')
        if type_name not in profile['types_seen']:
            profile['types_seen'].append(type_name)

        if v.get('in_drill_zone'):
            profile['drill_zone_snapshots'] += 1

        if v.get('in_fishing_hotspot'):
            profile['fishing_hotspot_snapshots'] += 1

        # 保留最近 30 筆快照摘要（用於 going dark 偵測）
        profile['snapshots'].append({
            'time': now,
            'lat': v['lat'],
            'lon': v['lon'],
            'zone': v.get('in_drill_zone'),
            'hotspot': v.get('in_fishing_hotspot'),
            'speed': v.get('speed', 0),
            'name': name,
        })
        profile['snapshots'] = profile['snapshots'][-30:]

    # 清理超過 30 天未出現的船隻
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    history = {k: v for k, v in history.items() if v['last_seen'] >= cutoff}

    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"📜 已更新船隻歷史: {len(history)} 艘追蹤中")


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
            'in_drill_zone_count': sum(stats['in_drill_zones'].values()),
            'suspicious_count': stats['suspicious_count'],
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
    print(f"   軍演區內: {sum(stats['in_drill_zones'].values())}")
    print(f"   可疑船隻: {stats['suspicious_count']}")
    print(f"   平均航速: {stats['avg_speed']} kn")
    print(f"   類型分布: {stats['by_type']}")

    # 累積歷史記錄
    update_vessel_history(vessels)

    # 儲存資料
    save_data(vessels, stats)

    print("\n✅ 完成!")


if __name__ == '__main__':
    asyncio.run(main())
