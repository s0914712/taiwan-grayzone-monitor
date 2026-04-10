#!/usr/bin/env python3
"""
================================================================================
GFW SAR 暗船偵測資料擷取腳本
Dark Vessel SAR Data Fetcher (Global Fishing Watch v3 API)
================================================================================

功能：
1. 從 GFW v3 API 擷取 30 天 SAR 衛星偵測資料（4 個子區域）
2. 按區域與日期彙總，計算暗船比例
3. 輸出 data/dark_vessels.json，供 analyze_suspicious /
   exercise_prediction / 前端 dark-vessels.html 使用

API:
  POST https://gateway.api.globalfishingwatch.org/v3/4wings/report
  dataset: public-global-sar-presence:latest
  需設定環境變數 GFW_API_TOKEN

輸出格式（與既有檔案保持一致）：
  {
    "updated_at": ISO8601,
    "data_range": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
    "overall": {
      "total_detections": int,
      "dark_vessels": int,
      "dark_ratio": float,
      "dark_by_date": {"YYYY-MM-DD": int, ...}
    },
    "regions": {
      "<region_id>": {
        "name": str,
        "total_detections": int,
        "dark_vessels": int,
        "matched_vessels": int,
        "dark_ratio": float,
        "dark_by_date": {...},
        "matched_by_flag": {...},
        "dark_details": [{"lat", "lon", "date", "detections"}, ...]
      }
    }
  }
================================================================================
"""

import os
import sys
import json
import time
import requests
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# =============================================================================
# 設定
# =============================================================================

API_TOKEN = os.environ.get('GFW_API_TOKEN', '').strip()
BASE_URL = "https://gateway.api.globalfishingwatch.org/v3"

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_PATH = DATA_DIR / 'dark_vessels.json'

# 滾動資料窗口（天）
DATA_RANGE_DAYS = 30

# 每個區域保留的暗船座標樣本上限（供前端地圖顯示）
MAX_DETAILS_PER_REGION = 100

# =============================================================================
# 區域定義（與 fetch_weekly_dark_vessels.py 同步）
# - south_china_sea 南界由 18°N 擴展至 15°N，涵蓋更廣的南海北部海域
# =============================================================================

DARK_VESSEL_REGIONS = {
    "taiwan_strait": {
        "name": "台灣海峽",
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [118.0, 23.5], [122.0, 23.5], [122.0, 26.5],
                [118.0, 26.5], [118.0, 23.5]
            ]]
        }
    },
    "east_taiwan": {
        "name": "台灣東部海域",
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [121.5, 22.0], [124.0, 22.0], [124.0, 25.5],
                [121.5, 25.5], [121.5, 22.0]
            ]]
        }
    },
    "south_china_sea": {
        "name": "南海北部",
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [110.0, 15.0], [118.0, 15.0], [118.0, 23.0],
                [110.0, 23.0], [110.0, 15.0]
            ]]
        }
    },
    "east_china_sea": {
        "name": "東海",
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [122.0, 26.0], [130.5, 26.0], [130.5, 34.0],
                [122.0, 34.0], [122.0, 26.0]
            ]]
        }
    }
}

# =============================================================================
# GFW API
# =============================================================================

def get_headers():
    return {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }


def fetch_sar_data(region_geojson, start_date, end_date):
    """呼叫 GFW /v3/4wings/report 取得 SAR 偵測記錄

    回傳：扁平化後的 record 列表（每筆 dict 包含 lat/lon/date/detections/vesselId 等）
    """
    params = {
        "datasets[0]": "public-global-sar-presence:latest",
        "date-range": f"{start_date},{end_date}",
        "temporal-resolution": "DAILY",
        "spatial-resolution": "HIGH",
        "spatial-aggregation": "false",
        "format": "JSON"
    }

    try:
        resp = requests.post(
            f"{BASE_URL}/4wings/report",
            params=params,
            json={"geojson": region_geojson},
            headers=get_headers(),
            timeout=180
        )
    except requests.RequestException as e:
        print(f"   ❌ 請求失敗: {e}")
        return None  # None 表示請求失敗（非 0 筆記錄）

    if resp.status_code != 200:
        print(f"   ❌ API 錯誤 {resp.status_code}: {resp.text[:200]}")
        return None

    try:
        data = resp.json()
    except ValueError as e:
        print(f"   ❌ JSON 解析失敗: {e}")
        return None

    entries = data.get('entries', [])
    records = []
    for entry in entries:
        for key, values in entry.items():
            if isinstance(values, list):
                records.extend(values)
    return records


# =============================================================================
# 彙整
# =============================================================================

def summarize_region(records):
    """彙整單一區域的 SAR 記錄為輸出 schema"""
    total = 0
    dark = 0
    matched = 0
    dark_by_date = defaultdict(int)
    matched_by_flag = defaultdict(int)
    dark_details = []

    for r in records:
        total += 1
        date_str = (r.get('date') or '')[:10]
        is_dark = not r.get('vesselId')

        if is_dark:
            dark += 1
            if date_str:
                dark_by_date[date_str] += 1

            if len(dark_details) < MAX_DETAILS_PER_REGION:
                lat = r.get('lat', r.get('latitude'))
                lon = r.get('lon', r.get('longitude'))
                if lat is not None and lon is not None:
                    dark_details.append({
                        'lat': round(float(lat), 2),
                        'lon': round(float(lon), 2),
                        'date': date_str,
                        'detections': r.get('detections', 1),
                    })
        else:
            matched += 1
            flag = r.get('flag') or r.get('vesselFlag') or 'UNKNOWN'
            matched_by_flag[flag] += 1

    dark_ratio = round(dark / total * 100, 1) if total > 0 else 0.0

    return {
        'total_detections': total,
        'dark_vessels': dark,
        'matched_vessels': matched,
        'dark_ratio': dark_ratio,
        'dark_by_date': dict(sorted(dark_by_date.items())),
        'matched_by_flag': dict(matched_by_flag),
        'dark_details': dark_details,
    }


# =============================================================================
# 主程式
# =============================================================================

def main():
    print("=" * 70)
    print("🛰️  GFW SAR 暗船偵測資料擷取")
    print(f"執行時間: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    print("=" * 70)

    if not API_TOKEN:
        print("⚠️ 未設定 GFW_API_TOKEN，跳過（保留既有 dark_vessels.json）")
        return

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=DATA_RANGE_DAYS)
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    print(f"📅 查詢範圍: {start_str} ~ {end_str} ({DATA_RANGE_DAYS} 天)\n")

    regions_out = {}
    overall_total = 0
    overall_dark = 0
    overall_dark_by_date = defaultdict(int)
    any_success = False

    for region_id, region_info in DARK_VESSEL_REGIONS.items():
        print(f"📍 {region_info['name']} ({region_id})")
        records = fetch_sar_data(region_info['geojson'], start_str, end_str)

        if records is None:
            # 請求失敗 → 此區域留空，繼續處理其他區域
            regions_out[region_id] = {
                'name': region_info['name'],
                'total_detections': 0,
                'dark_vessels': 0,
                'matched_vessels': 0,
                'dark_ratio': 0.0,
                'dark_by_date': {},
                'matched_by_flag': {},
                'dark_details': [],
                'error': 'fetch_failed',
            }
            time.sleep(2)
            continue

        print(f"   取得 {len(records)} 筆 SAR 記錄")
        any_success = True

        summary = summarize_region(records)
        summary['name'] = region_info['name']
        regions_out[region_id] = summary

        overall_total += summary['total_detections']
        overall_dark += summary['dark_vessels']
        for d, c in summary['dark_by_date'].items():
            overall_dark_by_date[d] += c

        print(
            f"   暗船: {summary['dark_vessels']}/{summary['total_detections']} "
            f"({summary['dark_ratio']}%)"
        )
        time.sleep(2)  # 尊重 API 速率限制

    if not any_success:
        print("\n❌ 所有區域 API 請求皆失敗，保留既有 dark_vessels.json（不覆寫）")
        sys.exit(0)

    overall_ratio = (
        round(overall_dark / overall_total * 100, 1) if overall_total > 0 else 0.0
    )

    output = {
        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'data_range': {
            'start': start_str,
            'end': end_str,
        },
        'overall': {
            'total_detections': overall_total,
            'dark_vessels': overall_dark,
            'dark_ratio': overall_ratio,
            'dark_by_date': dict(sorted(overall_dark_by_date.items())),
        },
        'regions': regions_out,
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print(f"✅ 已儲存: {OUTPUT_PATH}")
    print(f"   總偵測: {overall_total}")
    print(f"   暗船: {overall_dark} ({overall_ratio}%)")
    print(f"   資料天數: {len(overall_dark_by_date)}")
    print(f"   檔案大小: {OUTPUT_PATH.stat().st_size / 1024:.1f} KB")
    print("=" * 70)


if __name__ == "__main__":
    main()
