#!/usr/bin/env python3
"""
================================================================================
暗船動畫資料擷取腳本
Dark Vessel Animation Data Fetcher
================================================================================

功能：
1. 從 GFW API 擷取 90 天 SAR 衛星偵測資料（4 個子區域）
2. 按實際偵測日分組（跳過無資料的日期）
3. 輸出 JSON 供前端動畫頁面使用

輸出：data/weekly_dark_vessels.json
================================================================================
"""

import os
import sys
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

OUTPUT_PATH = DATA_DIR / 'weekly_dark_vessels.json'

# 資料天數範圍（預設 90 天）
DATA_RANGE_DAYS = 90

# =============================================================================
# 區域定義（與 fetch_gfw_data.py 同步，東海已擴展至 34°N/130.5°E）
# =============================================================================

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

# =============================================================================
# API 函數
# =============================================================================

def get_headers():
    return {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }


def fetch_sar_data(region_geojson, start_date, end_date):
    """擷取 SAR 衛星偵測資料"""
    params = {
        "datasets[0]": "public-global-sar-presence:latest",
        "date-range": f"{start_date},{end_date}",
        "temporal-resolution": "DAILY",
        "spatial-resolution": "HIGH",
        "spatial-aggregation": "false",
        "format": "JSON"
    }

    try:
        response = requests.post(
            f"{BASE_URL}/4wings/report",
            params=params,
            json={"geojson": region_geojson},
            headers=get_headers(),
            timeout=180
        )

        if response.status_code == 200:
            data = response.json()
            entries = data.get('entries', [])
            records = []
            for entry in entries:
                for key, values in entry.items():
                    if isinstance(values, list):
                        records.extend(values)
            return records
        else:
            print(f"   ❌ API 錯誤 {response.status_code}: {response.text[:200]}")
            return []

    except Exception as e:
        print(f"   ❌ 請求失敗: {e}")
        return []


# =============================================================================
# 主程式
# =============================================================================

def main():
    print("=" * 70)
    print("🎬 暗船動畫資料擷取 - Dark Vessel Animation Data")
    print("=" * 70)
    print(f"執行時間: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

    if not API_TOKEN:
        print("⚠️ 未設定 GFW_API_TOKEN，跳過")
        return

    # 12 小時新鮮度檢查
    if OUTPUT_PATH.exists():
        mod_time = datetime.fromtimestamp(OUTPUT_PATH.stat().st_mtime)
        age_hours = (datetime.now() - mod_time).total_seconds() / 3600
        if age_hours < 12:
            print(f"⏭️ {OUTPUT_PATH} 尚新（{age_hours:.1f}h），跳過重新擷取")
            return

    # 計算日期範圍
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=DATA_RANGE_DAYS)
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')

    print(f"📅 查詢範圍: {start_str} ~ {end_str} ({DATA_RANGE_DAYS} 天)")

    # 收集所有區域的暗船記錄
    all_records = []  # (region_id, region_name, record)

    for region_id, region_info in DARK_VESSEL_REGIONS.items():
        print(f"\n📍 {region_info['name']}...")
        records = fetch_sar_data(region_info['geojson'], start_str, end_str)
        print(f"   取得 {len(records)} 筆 SAR 記錄")

        for r in records:
            all_records.append((region_id, region_info['name'], r))

        time.sleep(2)  # API 速率限制

    print(f"\n📊 總共取得 {len(all_records)} 筆記錄")

    # 按日期分組
    days_map = {}  # date -> {regions -> {dark_count, total_count, points}}

    for region_id, region_name, record in all_records:
        date = record.get('date', '')[:10]
        if not date:
            continue

        is_dark = not record.get('vesselId')

        if date not in days_map:
            days_map[date] = {}

        if region_id not in days_map[date]:
            days_map[date][region_id] = {
                'name': region_name,
                'dark_count': 0,
                'total_count': 0,
                'points': []
            }

        region_data = days_map[date][region_id]
        region_data['total_count'] += 1

        if is_dark:
            region_data['dark_count'] += 1
            lat = record.get('lat', record.get('latitude'))
            lon = record.get('lon', record.get('longitude'))
            if lat is not None and lon is not None:
                region_data['points'].append({
                    'lat': round(lat, 2),
                    'lon': round(lon, 2),
                    'detections': record.get('detections', 1)
                })

    # 組裝輸出 JSON
    days_list = []
    for date in sorted(days_map.keys()):
        regions = days_map[date]
        total_detections = sum(r['total_count'] for r in regions.values())
        dark_vessels = sum(r['dark_count'] for r in regions.values())
        point_count = sum(len(r['points']) for r in regions.values())

        days_list.append({
            'date': date,
            'summary': {
                'total_detections': total_detections,
                'dark_vessels': dark_vessels,
                'dark_ratio': round(dark_vessels / total_detections * 100, 1) if total_detections > 0 else 0,
                'point_count': point_count
            },
            'regions': regions
        })

    output = {
        'updated_at': datetime.utcnow().isoformat() + 'Z',
        'date_range': {
            'start': start_str,
            'end': end_str
        },
        'total_days': len(days_list),
        'days': days_list
    }

    # 儲存
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 動畫資料已儲存: {OUTPUT_PATH}")
    print(f"   偵測天數: {len(days_list)}")
    total_points = sum(d['summary']['point_count'] for d in days_list)
    print(f"   暗船座標點: {total_points}")
    print(f"   檔案大小: {OUTPUT_PATH.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
