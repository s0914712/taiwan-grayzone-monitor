#!/usr/bin/env python3
"""
================================================================================
GFW 資料擷取腳本 - 台灣周邊可疑船隻監測
Taiwan Gray Zone Vessel Monitor - GFW Data Fetcher
================================================================================

功能：
1. 從 GFW API 擷取台灣周邊 SAR 衛星偵測資料（暗船）
2. 擷取中國籍船隻在台灣周邊的存在資料（Vessel Presence）
3. 擷取漁撈努力量資料（Fishing Effort）
4. 計算可疑船隻指標並儲存至 JSON

資料來源：
- Global Fishing Watch API (4wings report)
  - public-global-sar-presence:latest
  - public-global-presence:latest (flag=CHN)
  - public-global-fishing-effort:latest
================================================================================
"""

import os
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path

# =============================================================================
# 設定
# =============================================================================

API_TOKEN = os.environ.get('GFW_API_TOKEN', '').strip()
BASE_URL = "https://gateway.api.globalfishingwatch.org/v3"

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# 台灣周邊監測區域
TAIWAN_AREA = {
    "type": "Polygon",
    "coordinates": [[
        [117.0, 21.0], [126.0, 21.0], [126.0, 27.0], [117.0, 27.0], [117.0, 21.0]
    ]]
}

# 軍演區域定義（Joint Sword 等）
DRILL_ZONES = {
    "north": {"name": "北區", "coords": [[121.0, 25.5], [122.5, 25.5], [122.5, 26.8], [121.0, 26.8], [121.0, 25.5]]},
    "east": {"name": "東區", "coords": [[122.5, 23.0], [125.0, 23.0], [125.0, 25.5], [122.5, 25.5], [122.5, 23.0]]},
    "south": {"name": "南區", "coords": [[119.0, 21.5], [121.0, 21.5], [121.0, 23.0], [119.0, 23.0], [119.0, 21.5]]},
    "west": {"name": "西區", "coords": [[118.5, 23.5], [120.0, 23.5], [120.0, 25.0], [118.5, 25.0], [118.5, 23.5]]},
}

# =============================================================================
# API 函數
# =============================================================================

def get_headers():
    """Build request headers (deferred so token is read at call time)"""
    return {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }


def fetch_4wings_report(dataset, region, start_date, end_date, filters=None):
    """
    通用 4wings report API 呼叫
    dataset: GFW dataset ID (e.g. 'public-global-sar-presence:latest')
    filters: optional list of filter strings (e.g. ["flag='CHN'"])
    """
    params = {
        "datasets[0]": dataset,
        "date-range": f"{start_date},{end_date}",
        "temporal-resolution": "DAILY",
        "spatial-resolution": "HIGH",
        "spatial-aggregation": "false",
        "format": "JSON"
    }

    if filters:
        for i, f in enumerate(filters):
            params[f"filters[{i}]"] = f

    try:
        response = requests.post(
            f"{BASE_URL}/4wings/report",
            params=params,
            json={"geojson": region},
            headers=get_headers(),
            timeout=120
        )

        if response.status_code == 200:
            return response.json()
        else:
            print(f"   ❌ API 錯誤 {response.status_code}: {response.text[:300]}")
            return {}

    except Exception as e:
        print(f"   ❌ 請求失敗: {e}")
        return {}


def parse_4wings_entries(data):
    """解析 4wings API 回應的 entries"""
    entries = data.get('entries', [])
    if not entries:
        return []

    results = []
    for entry in entries:
        for key, values in entry.items():
            if isinstance(values, list):
                results.extend(values)
    return results


# =============================================================================
# 資料擷取函數
# =============================================================================

def fetch_sar_data(region, start_date, end_date):
    """擷取 SAR 衛星偵測資料（暗船偵測）"""
    print("   🛰️ SAR 衛星偵測...")
    data = fetch_4wings_report(
        "public-global-sar-presence:latest",
        region, start_date, end_date
    )
    records = parse_4wings_entries(data)
    print(f"      取得 {len(records)} 筆 SAR 記錄")
    return records


def fetch_vessel_presence(region, start_date, end_date):
    """擷取中國籍船隻存在資料（CHN flag filter）"""
    print("   🚢 中國籍船隻存在...")
    data = fetch_4wings_report(
        "public-global-presence:latest",
        region, start_date, end_date,
        filters=["flag='CHN'"]
    )
    records = parse_4wings_entries(data)
    print(f"      取得 {len(records)} 筆中國船隻記錄")
    return records


def fetch_fishing_effort(region, start_date, end_date):
    """擷取漁撈努力量資料"""
    print("   🎣 漁撈努力量...")
    data = fetch_4wings_report(
        "public-global-fishing-effort:latest",
        region, start_date, end_date
    )
    records = parse_4wings_entries(data)
    print(f"      取得 {len(records)} 筆漁撈記錄")
    return records


# =============================================================================
# 分析函數
# =============================================================================

def is_in_drill_zone(lat, lon):
    """檢查座標是否在任何軍演區內"""
    for zone_id, zone in DRILL_ZONES.items():
        coords = zone['coords']
        # coords 格式: [[lon, lat], ...]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        if min(lats) <= lat <= max(lats) and min(lons) <= lon <= max(lons):
            return zone_id
    return None


def analyze_sar_daily(sar_records):
    """將 SAR 記錄彙整為每日統計"""
    daily_stats = {}
    for record in sar_records:
        date = record.get('date', '')[:10]
        if not date:
            continue

        if date not in daily_stats:
            daily_stats[date] = {
                'date': date,
                'total_detections': 0,
                'dark_vessels': 0,
            }

        daily_stats[date]['total_detections'] += 1

        # 暗船：無 vessel ID 的偵測
        if not record.get('vesselId'):
            daily_stats[date]['dark_vessels'] += 1

    return sorted(daily_stats.values(), key=lambda x: x['date'])


def analyze_presence(presence_records):
    """分析中國船隻在台灣周邊的存在情況"""
    daily_presence = {}
    drill_zone_records = 0
    total_hours = 0

    for record in presence_records:
        date = record.get('date', '')[:10]
        if not date:
            continue

        hours = record.get('hours', record.get('value', 0))
        if not isinstance(hours, (int, float)):
            hours = 0

        if date not in daily_presence:
            daily_presence[date] = {
                'date': date,
                'chn_vessel_hours': 0,
                'in_drill_zone_hours': 0,
            }

        daily_presence[date]['chn_vessel_hours'] += hours
        total_hours += hours

        # 檢查是否在軍演區
        lat = record.get('lat', record.get('latitude'))
        lon = record.get('lon', record.get('longitude'))
        if lat is not None and lon is not None:
            zone = is_in_drill_zone(lat, lon)
            if zone:
                daily_presence[date]['in_drill_zone_hours'] += hours
                drill_zone_records += 1

    return {
        'daily': sorted(daily_presence.values(), key=lambda x: x['date']),
        'total_records': len(presence_records),
        'total_hours': round(total_hours, 1),
        'drill_zone_records': drill_zone_records,
    }


def analyze_fishing(fishing_records):
    """分析漁撈努力量"""
    daily_effort = {}
    total_hours = 0

    for record in fishing_records:
        date = record.get('date', '')[:10]
        if not date:
            continue

        hours = record.get('hours', record.get('value', 0))
        if not isinstance(hours, (int, float)):
            hours = 0

        if date not in daily_effort:
            daily_effort[date] = {
                'date': date,
                'fishing_hours': 0,
            }

        daily_effort[date]['fishing_hours'] += hours
        total_hours += hours

    return {
        'daily': sorted(daily_effort.values(), key=lambda x: x['date']),
        'total_fishing_hours': round(total_hours, 1),
    }


# =============================================================================
# 主程式
# =============================================================================

def main():
    print("=" * 60)
    print("🛰️ GFW 資料擷取 - 台灣周邊可疑船隻監測")
    print("=" * 60)
    print(f"執行時間: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

    if not API_TOKEN:
        print("⚠️ 未設定 GFW_API_TOKEN，跳過 GFW 資料收集")
        return

    # 計算日期範圍（最近 30 天）
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=30)

    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')

    print(f"\n📅 查詢範圍: {start_str} ~ {end_str}")
    print(f"\n📡 擷取 GFW 資料（三組資料集）...")

    # 擷取三組資料
    sar_records = fetch_sar_data(TAIWAN_AREA, start_str, end_str)
    presence_records = fetch_vessel_presence(TAIWAN_AREA, start_str, end_str)
    fishing_records = fetch_fishing_effort(TAIWAN_AREA, start_str, end_str)

    # 分析各資料集
    daily_list = analyze_sar_daily(sar_records)
    presence_analysis = analyze_presence(presence_records)
    fishing_analysis = analyze_fishing(fishing_records)

    # 計算暗船趨勢
    if len(daily_list) >= 7:
        recent_7d = sum(d['dark_vessels'] for d in daily_list[-7:]) / 7
        previous_7d = sum(d['dark_vessels'] for d in daily_list[-14:-7]) / 7 if len(daily_list) >= 14 else recent_7d
        trend = ((recent_7d - previous_7d) / previous_7d * 100) if previous_7d > 0 else 0
    else:
        recent_7d = 0
        trend = 0

    # 建立輸出資料
    output = {
        'updated_at': datetime.utcnow().isoformat() + 'Z',
        'data_range': {
            'start': start_str,
            'end': end_str
        },
        'summary': {
            'total_days': len(daily_list),
            'avg_daily_detections': sum(d['total_detections'] for d in daily_list) / len(daily_list) if daily_list else 0,
            'avg_daily_dark_vessels': sum(d['dark_vessels'] for d in daily_list) / len(daily_list) if daily_list else 0,
            'recent_7d_avg': recent_7d,
            'trend_pct': trend,
            'chn_presence_records': presence_analysis['total_records'],
            'chn_presence_hours': presence_analysis['total_hours'],
            'chn_drill_zone_records': presence_analysis['drill_zone_records'],
            'total_fishing_hours': fishing_analysis['total_fishing_hours'],
        },
        'daily': daily_list,
        'chn_presence': presence_analysis,
        'fishing_effort': fishing_analysis,
        'drill_zones': DRILL_ZONES,
        'alerts': []
    }

    # 檢查暗船異常
    if len(daily_list) >= 2:
        latest = daily_list[-1]
        avg = output['summary']['avg_daily_dark_vessels']
        if avg > 0 and latest['dark_vessels'] > avg * 1.5:
            output['alerts'].append({
                'type': 'high_dark_vessels',
                'date': latest['date'],
                'value': latest['dark_vessels'],
                'threshold': avg * 1.5,
                'message': f"暗船數量異常增加: {latest['dark_vessels']} (平均 {avg:.0f})"
            })

    # 檢查中國船隻軍演區活動
    if presence_analysis['drill_zone_records'] > 0:
        output['alerts'].append({
            'type': 'chn_drill_zone_presence',
            'value': presence_analysis['drill_zone_records'],
            'message': f"中國船隻在軍演區活動: {presence_analysis['drill_zone_records']} 筆記錄"
        })

    # 儲存資料
    output_path = DATA_DIR / 'vessel_data.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 資料已儲存: {output_path}")
    print(f"   SAR 偵測: {len(sar_records)} 筆")
    print(f"   中國船隻: {presence_analysis['total_records']} 筆 "
          f"(軍演區 {presence_analysis['drill_zone_records']} 筆)")
    print(f"   漁撈努力: {fishing_analysis['total_fishing_hours']:.0f} 小時")
    print(f"   暗船趨勢: {trend:+.1f}%")


if __name__ == "__main__":
    main()
