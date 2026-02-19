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
4. 多區域暗船偵測與分析
5. 計算可疑船隻指標並儲存至 JSON

資料來源：
- Global Fishing Watch API (4wings report)
  - public-global-sar-presence:latest
  - public-global-presence:latest (flag=CHN)
  - public-global-fishing-effort:latest
================================================================================
"""

import os
import json
import time
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

# 台灣周邊監測區域（總區域，含東海延伸至 34°N）
TAIWAN_AREA = {
    "type": "Polygon",
    "coordinates": [[
        [117.0, 21.0], [130.5, 21.0], [130.5, 34.0], [117.0, 34.0], [117.0, 21.0]
    ]]
}

# 暗船偵測子區域
DARK_VESSEL_REGIONS = {
    "taiwan_strait": {
        "name": "台灣海峽",
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [118.0, 23.5], [122.0, 23.5], [122.0, 26.5], [118.0, 26.5], [118.0, 23.5]
            ]]
        }
    },
    "east_taiwan": {
        "name": "台灣東部海域",
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [121.5, 22.0], [124.0, 22.0], [124.0, 25.5], [121.5, 25.5], [121.5, 22.0]
            ]]
        }
    },
    "south_china_sea": {
        "name": "南海北部",
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [110.0, 18.0], [118.0, 18.0], [118.0, 23.0], [110.0, 23.0], [110.0, 18.0]
            ]]
        }
    },
    "east_china_sea": {
        "name": "東海",
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [122.0, 26.0], [130.5, 26.0], [130.5, 34.0], [122.0, 34.0], [122.0, 26.0]
            ]]
        }
    }
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
    """Build request headers"""
    return {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }


def fetch_4wings_report(dataset, region, start_date, end_date, filters=None,
                        spatial_resolution="HIGH", spatial_aggregation="false",
                        group_by=None):
    """
    通用 4wings report API 呼叫
    """
    params = {
        "datasets[0]": dataset,
        "date-range": f"{start_date},{end_date}",
        "temporal-resolution": "DAILY",
        "spatial-resolution": spatial_resolution,
        "spatial-aggregation": spatial_aggregation,
        "format": "JSON"
    }

    if filters:
        for i, f in enumerate(filters):
            params[f"filters[{i}]"] = f

    if group_by:
        params["group-by"] = group_by

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
    """擷取 SAR 衛星偵測資料（全部）"""
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


def fetch_fishing_effort_by_flag(region, start_date, end_date):
    """擷取漁撈努力量（按國旗分組）"""
    print("   🎣 漁撈努力量（按國旗）...")
    data = fetch_4wings_report(
        "public-global-fishing-effort:latest",
        region, start_date, end_date,
        spatial_resolution="LOW",
        spatial_aggregation="true",
        group_by="FLAG"
    )
    records = parse_4wings_entries(data)
    print(f"      取得 {len(records)} 筆記錄")
    return records


# =============================================================================
# 分析函數
# =============================================================================

def is_in_drill_zone(lat, lon):
    """檢查座標是否在任何軍演區內"""
    for zone_id, zone in DRILL_ZONES.items():
        coords = zone['coords']
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
# 暗船偵測（多區域）
# =============================================================================

def detect_dark_vessels_in_region(region_geojson, start_date, end_date):
    """
    偵測指定區域的暗船
    暗船定義：SAR 偵測到但無 AIS 匹配（vesselId 為空）
    """
    records = fetch_sar_data(region_geojson, start_date, end_date)

    dark_vessels = []
    matched_vessels = []

    for d in records:
        vessel_id = d.get('vesselId', '')
        if not vessel_id:
            dark_vessels.append(d)
        else:
            matched_vessels.append(d)

    # 暗船按日期分組
    dark_by_date = {}
    for d in dark_vessels:
        date = d.get('date', '')[:10]
        if date:
            dark_by_date[date] = dark_by_date.get(date, 0) + d.get('detections', 1)

    # 有 AIS 的船隻按國旗分組
    matched_by_flag = {}
    for d in matched_vessels:
        flag = d.get('flag', 'Unknown') or 'Unknown'
        matched_by_flag[flag] = matched_by_flag.get(flag, 0) + d.get('detections', 1)

    # 暗船位置詳情（限制前 100 筆避免資料過大）
    dark_details = []
    for d in dark_vessels[:100]:
        lat = d.get('lat', d.get('latitude'))
        lon = d.get('lon', d.get('longitude'))
        if lat is not None and lon is not None:
            dark_details.append({
                'lat': lat,
                'lon': lon,
                'date': d.get('date', '')[:10],
                'detections': d.get('detections', 1),
            })

    total = len(records)
    return {
        'total_detections': total,
        'dark_vessels': len(dark_vessels),
        'matched_vessels': len(matched_vessels),
        'dark_ratio': round(len(dark_vessels) / total * 100, 1) if total > 0 else 0,
        'dark_by_date': dict(sorted(dark_by_date.items())),
        'matched_by_flag': dict(sorted(matched_by_flag.items(), key=lambda x: x[1], reverse=True)),
        'dark_details': dark_details,
    }


def run_dark_vessel_analysis(start_date, end_date):
    """
    對所有監測區域執行暗船偵測分析
    """
    print("\n🔦 暗船偵測分析（多區域）...")

    regions_result = {}
    overall_dark = 0
    overall_total = 0
    overall_dark_by_date = {}

    for region_id, region_info in DARK_VESSEL_REGIONS.items():
        print(f"\n   📍 {region_info['name']}...")
        result = detect_dark_vessels_in_region(
            region_info['geojson'], start_date, end_date
        )
        result['name'] = region_info['name']
        regions_result[region_id] = result

        overall_dark += result['dark_vessels']
        overall_total += result['total_detections']

        # 合併日期統計
        for date, count in result['dark_by_date'].items():
            overall_dark_by_date[date] = overall_dark_by_date.get(date, 0) + count

        print(f"      總偵測: {result['total_detections']}, "
              f"暗船: {result['dark_vessels']}, "
              f"比例: {result['dark_ratio']}%")

        # 避免 API 速率限制
        time.sleep(2)

    output = {
        'updated_at': datetime.utcnow().isoformat() + 'Z',
        'data_range': {'start': start_date, 'end': end_date},
        'overall': {
            'total_detections': overall_total,
            'dark_vessels': overall_dark,
            'dark_ratio': round(overall_dark / overall_total * 100, 1) if overall_total > 0 else 0,
            'dark_by_date': dict(sorted(overall_dark_by_date.items())),
        },
        'regions': regions_result,
    }

    # 儲存暗船資料
    output_path = DATA_DIR / 'dark_vessels.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n   ✅ 暗船資料已儲存: {output_path}")
    print(f"      總偵測: {overall_total}, 暗船: {overall_dark}, "
          f"比例: {output['overall']['dark_ratio']}%")

    return output


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

    # ── 第一部分：總區域三組資料集 ──
    print(f"\n📡 擷取 GFW 資料（三組資料集）...")

    sar_records = fetch_sar_data(TAIWAN_AREA, start_str, end_str)
    presence_records = fetch_vessel_presence(TAIWAN_AREA, start_str, end_str)
    fishing_records = fetch_fishing_effort(TAIWAN_AREA, start_str, end_str)

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

    output = {
        'updated_at': datetime.utcnow().isoformat() + 'Z',
        'data_range': {'start': start_str, 'end': end_str},
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

    if presence_analysis['drill_zone_records'] > 0:
        output['alerts'].append({
            'type': 'chn_drill_zone_presence',
            'value': presence_analysis['drill_zone_records'],
            'message': f"中國船隻在軍演區活動: {presence_analysis['drill_zone_records']} 筆記錄"
        })

    output_path = DATA_DIR / 'vessel_data.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 資料已儲存: {output_path}")
    print(f"   SAR 偵測: {len(sar_records)} 筆")
    print(f"   中國船隻: {presence_analysis['total_records']} 筆 "
          f"(軍演區 {presence_analysis['drill_zone_records']} 筆)")
    print(f"   漁撈努力: {fishing_analysis['total_fishing_hours']:.0f} 小時")
    print(f"   暗船趨勢: {trend:+.1f}%")

    # ── 第二部分：多區域暗船偵測 ──
    run_dark_vessel_analysis(start_str, end_str)


if __name__ == "__main__":
    main()
