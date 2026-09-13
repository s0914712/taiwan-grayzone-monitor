#!/usr/bin/env python3
"""
================================================================================
GFW SAR 暗船偵測資料擷取腳本 — 全區單次擷取版
Dark Vessel SAR Data Fetcher (Global Fishing Watch v3 API)
================================================================================

功能：
1. 以單一矩形（TAIWAN_BBOX）呼叫 GFW v3 API 一次取回完整 SAR 偵測
2. 依日期彙整暗船比例
3. 額外在 `taiwan_region.sub_zones` 保留四個方位子區域的計數，
   讓前端側邊欄（north/east/south/west）照常顯示
4. 輸出 data/dark_vessels.json，供 analyze_suspicious /
   exercise_prediction / 前端 dark-vessels.html 使用

API:
  POST https://gateway.api.globalfishingwatch.org/v3/4wings/report
  dataset: public-global-sar-presence:latest
  需設定環境變數 GFW_API_TOKEN

輸出格式：
  {
    "updated_at": ISO8601,
    "bbox": {"lat_min", "lat_max", "lon_min", "lon_max"},
    "data_range": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
    "overall": {
      "total_detections": int,
      "dark_vessels": int,
      "dark_ratio": float,
      "dark_by_date": {"YYYY-MM-DD": int, ...},
      "total_by_date": {"YYYY-MM-DD": int, ...}
    },
    "regions": {
      "taiwan_region": {
        "name": "台灣周邊海域",
        "total_detections": int,
        "dark_vessels": int,
        "matched_vessels": int,
        "dark_ratio": float,
        "dark_by_date": {...},
        "total_by_date": {...},
        "matched_by_flag": {...},
        "dark_details": [{"lat","lon","date","detections"}, ...],
        "sub_zones": {
          "north": {"name","total_detections","dark_vessels","dark_ratio"},
          "east":  {...},
          "south": {...},
          "west":  {...}
        }
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

from io_utils import atomic_write_json, make_retry_session

# =============================================================================
# 設定
# =============================================================================

API_TOKEN = os.environ.get('GFW_API_TOKEN', '').strip()
BASE_URL = "https://gateway.api.globalfishingwatch.org/v3"

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_PATH = DATA_DIR / 'dark_vessels.json'
# 全量暗船偵測點（完整精度，供 match_sar_ais.py 以本地 AIS 重比對）
DETECTIONS_PATH = DATA_DIR / 'sar_detections.json'

# 滾動資料窗口（天）
DATA_RANGE_DAYS = 30

# 暗船座標樣本上限（供前端地圖顯示）
MAX_DETAILS = 400

# =============================================================================
# 全區 bounding box（覆蓋台灣周邊灰區、南海北部、東海）
# =============================================================================

TAIWAN_BBOX = {
    'lat_min': 15,
    'lat_max': 35,
    'lon_min': 110,
    'lon_max': 128,
}

REGION_NAME = "台灣周邊海域"


def previous_detection_total(path=OUTPUT_PATH):
    """讀既有 dark_vessels.json 的 overall.total_detections（讀不到就 0）"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return int((json.load(f).get('overall') or {}).get('total_detections', 0))
    except (OSError, ValueError, TypeError):
        return 0


def bbox_to_geojson(bbox):
    """將 bounding box dict 轉為 GFW API 需要的 GeoJSON Polygon"""
    lat_min, lat_max = bbox['lat_min'], bbox['lat_max']
    lon_min, lon_max = bbox['lon_min'], bbox['lon_max']
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon_min, lat_min],
            [lon_max, lat_min],
            [lon_max, lat_max],
            [lon_min, lat_max],
            [lon_min, lat_min],
        ]]
    }


# =============================================================================
# 子區域定義（僅用於 sub_zones 統計，不觸發額外 API 請求）
# 與前端 sidebar 的 zone-north/east/south/west 對應
# =============================================================================

SUB_ZONES = {
    "north": {
        "name": "東海 / 北方",
        "lat_min": 26.0, "lat_max": 34.0,
        "lon_min": 122.0, "lon_max": 130.5,
    },
    "east": {
        "name": "台灣東部",
        "lat_min": 22.0, "lat_max": 25.5,
        "lon_min": 121.5, "lon_max": 124.0,
    },
    "south": {
        "name": "南海北部",
        "lat_min": 15.0, "lat_max": 23.0,
        "lon_min": 110.0, "lon_max": 118.0,
    },
    "west": {
        "name": "台灣海峽",
        "lat_min": 23.5, "lat_max": 26.5,
        "lon_min": 118.0, "lon_max": 122.0,
    },
}


def point_in_zone(lat, lon, zone):
    return (
        zone['lat_min'] <= lat <= zone['lat_max']
        and zone['lon_min'] <= lon <= zone['lon_max']
    )


# =============================================================================
# GFW API
# =============================================================================

def get_headers():
    return {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }


def fetch_sar_data(region_geojson, start_date, end_date):
    """呼叫 GFW /v3/4wings/report 取得 SAR 偵測記錄

    回傳：扁平化後的 record 列表；None 表示請求失敗
    """
    params = {
        "datasets[0]": "public-global-sar-presence:latest",
        "date-range": f"{start_date},{end_date}",
        "temporal-resolution": "DAILY",
        "spatial-resolution": "HIGH",
        "spatial-aggregation": "false",
        "format": "JSON",
    }

    try:
        resp = make_retry_session().post(
            f"{BASE_URL}/4wings/report",
            params=params,
            json={"geojson": region_geojson},
            headers=get_headers(),
            timeout=300,
        )
    except requests.RequestException as e:
        print(f"   ❌ 請求失敗: {e}")
        return None

    if resp.status_code != 200:
        print(f"   ❌ API 錯誤 {resp.status_code}: {resp.text[:300]}")
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

def build_region_summary(records):
    """把扁平化 SAR records 彙整成單一 taiwan_region 結構"""
    total = 0
    dark = 0
    matched = 0
    dark_by_date = defaultdict(int)
    # 逐日「總」偵測數。Sentinel-1 不是每天過境、每次掃的幅寬也不同
    # （實測 2026-08-16 有 2,107 筆、隔天只有 25 筆），所以每日暗船「數量」
    # 主要反映衛星覆蓋而非船的行為；有了同分母才能畫出每日暗船「比例」。
    total_by_date = defaultdict(int)
    matched_by_flag = defaultdict(int)
    dark_details = []

    # 子區域計數
    sub_counts = {
        zone_id: {'total': 0, 'dark': 0}
        for zone_id in SUB_ZONES
    }

    for r in records:
        total += 1
        date_str = (r.get('date') or '')[:10]
        if date_str:
            total_by_date[date_str] += 1
        is_dark = not r.get('vesselId')

        lat = r.get('lat', r.get('latitude'))
        lon = r.get('lon', r.get('longitude'))
        try:
            lat_f = float(lat) if lat is not None else None
            lon_f = float(lon) if lon is not None else None
        except (TypeError, ValueError):
            lat_f = lon_f = None

        # 子區域統計
        if lat_f is not None and lon_f is not None:
            for zone_id, zone in SUB_ZONES.items():
                if point_in_zone(lat_f, lon_f, zone):
                    sub_counts[zone_id]['total'] += 1
                    if is_dark:
                        sub_counts[zone_id]['dark'] += 1
                    break

        if is_dark:
            dark += 1
            if date_str:
                dark_by_date[date_str] += 1

            if lat_f is not None and lon_f is not None and len(dark_details) < MAX_DETAILS:
                dark_details.append({
                    'lat': round(lat_f, 2),
                    'lon': round(lon_f, 2),
                    'date': date_str,
                    'detections': r.get('detections', 1),
                })
        else:
            matched += 1
            flag = r.get('flag') or r.get('vesselFlag') or 'UNKNOWN'
            matched_by_flag[flag] += 1

    dark_ratio = round(dark / total * 100, 1) if total > 0 else 0.0

    sub_zones_out = {}
    for zone_id, counts in sub_counts.items():
        zt = counts['total']
        zd = counts['dark']
        sub_zones_out[zone_id] = {
            'name': SUB_ZONES[zone_id]['name'],
            'total_detections': zt,
            'dark_vessels': zd,
            'dark_ratio': round(zd / zt * 100, 1) if zt > 0 else 0.0,
        }

    return {
        'name': REGION_NAME,
        'total_detections': total,
        'dark_vessels': dark,
        'matched_vessels': matched,
        'dark_ratio': dark_ratio,
        'dark_by_date': dict(sorted(dark_by_date.items())),
        'total_by_date': dict(sorted(total_by_date.items())),
        'matched_by_flag': dict(matched_by_flag),
        'dark_details': dark_details,
        'sub_zones': sub_zones_out,
    }


# GFW report 端點的偵測時刻欄位叫 entryTimestamp / exitTimestamp —— 不是
# timestamp／datetime（先前抓錯欄位名，導致 sar_detections.json 從來沒有一筆
# 帶時刻，match_sar_ais.pass_candidates() 的第一優先序永遠落空，只能退回
# s1_pass_times.json 的候選過境表：每天中位數 2 個、最多 4 個候選時刻，而
# gate 半徑 = 基礎誤差 + 速度×Δt，Δt 挑錯就整個放大）。
#
# 實測（2026-09 GFW v3 API 驗證）：date-range 縮到單日時，整批偵測共用同一個
# entryTimestamp，就是該次 Sentinel-1 過境時刻；但長區間查詢會把 entry/exit
# 壓平成整個查詢區間。本站一次查 30 天，所以只在下列條件都成立時才採用：
#   1. entryTimestamp == exitTimestamp（兩者不同 = 這列聚合了不只一次成像）
#   2. 時刻的日期 == 該列自己的 date（被壓平的時刻會落在別天，逐列擋掉）
# 區間起始日那天騙得過逐列檢查，故另有 drop_flattened_timestamps() 整批防呆。
TIMESTAMP_FIELDS = ('entryTimestamp', 'timestamp', 'datetime')


def extract_detection_timestamp(record, date_str):
    """回傳該筆偵測明確的成像時刻（ISO 字串）；無法確定時回 None。"""
    if not date_str:
        return None
    ts = None
    for field in TIMESTAMP_FIELDS:
        value = record.get(field)
        if value:
            ts = str(value)
            break
    if not ts:
        return None
    exit_ts = record.get('exitTimestamp')
    if exit_ts and str(exit_ts) != ts:
        return None          # 一列涵蓋多次成像 → 時刻不明確
    if ts[:10] != date_str[:10]:
        return None          # 被壓平成查詢區間
    return ts


def drop_flattened_timestamps(dark_records):
    """整批防呆：偵測橫跨多日、時刻卻全擠在同一天 → 判定被壓平，全數丟棄。

    就地修改 dark_records，回傳仍帶 timestamp 的筆數。
    """
    rec_dates = {r['date'] for r in dark_records if r.get('date')}
    ts_dates = {r['timestamp'][:10] for r in dark_records if r.get('timestamp')}
    if len(rec_dates) >= 3 and len(ts_dates) == 1:
        for r in dark_records:
            r.pop('timestamp', None)
        return 0
    return sum(1 for r in dark_records if r.get('timestamp'))


def build_dark_detection_records(records):
    """收集所有 GFW 標為 unmatched 的偵測點（完整精度，不截斷數量）。

    dark_vessels.json 的 dark_details 只留 400 筆，不足以做 SAR×AIS 重比對；
    此結構寫入 sar_detections.json 供 match_sar_ais.py 使用。座標為 API 在
    spatial-resolution=HIGH 下能給的最高精度（0.01° 網格中心）。

    偵測層級欄位（成像時刻 / 船長估計）在可確定時一併透傳，時刻的採用條件見
    extract_detection_timestamp()。
    """
    dark = []
    for r in records:
        if r.get('vesselId'):
            continue
        lat = r.get('lat', r.get('latitude'))
        lon = r.get('lon', r.get('longitude'))
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            continue
        rec = {
            'lat': round(lat_f, 5),
            'lon': round(lon_f, 5),
            'date': (r.get('date') or '')[:10],
            'detections': r.get('detections', 1),
        }
        ts = extract_detection_timestamp(r, rec['date'])
        if ts:
            rec['timestamp'] = ts
        length = r.get('length_m', r.get('length'))
        if length is not None:
            rec['length_m'] = length
        dark.append(rec)
    drop_flattened_timestamps(dark)
    return dark


# =============================================================================
# 主程式
# =============================================================================

def main():
    print("=" * 70)
    print("🛰️  GFW SAR 暗船偵測資料擷取（全區單次模式）")
    print(f"執行時間: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    print("=" * 70)

    if not API_TOKEN:
        print("⚠️ 未設定 GFW_API_TOKEN，跳過（保留既有 dark_vessels.json）")
        return

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=DATA_RANGE_DAYS)
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    print(f"📅 查詢範圍: {start_str} ~ {end_str} ({DATA_RANGE_DAYS} 天)")
    print(
        f"🗺  BBOX: lat {TAIWAN_BBOX['lat_min']}°~{TAIWAN_BBOX['lat_max']}°, "
        f"lon {TAIWAN_BBOX['lon_min']}°~{TAIWAN_BBOX['lon_max']}°\n"
    )

    geojson = bbox_to_geojson(TAIWAN_BBOX)
    print("📡 單次呼叫 /v3/4wings/report ...")
    records = fetch_sar_data(geojson, start_str, end_str)

    if records is None:
        print("\n❌ API 請求失敗，保留既有 dark_vessels.json（不覆寫）")
        sys.exit(0)

    # HTTP 200 但一筆都沒有 = 幾乎必然是上游暫時性問題（30 天視窗、整個台灣周邊
    # 海域不可能零偵測；2026-07-29 就這樣把 2,305 筆整份洗成 0，前端暗船頁全空
    # 但統計頁還留著累積歷史，看起來像前端壞掉）。既有檔案有資料就不覆寫。
    if not records:
        prev_total = previous_detection_total()
        if prev_total > 0:
            print(
                f"\n⚠️ API 回傳 0 筆（上次為 {prev_total} 筆），"
                "視為上游暫時性異常，保留既有 dark_vessels.json（不覆寫）"
            )
            sys.exit(0)
        print("\n⚠️ API 回傳 0 筆，且無既有資料可保留，照常寫出空結果")

    print(f"   取得 {len(records)} 筆 SAR 記錄")

    summary = build_region_summary(records)

    output = {
        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'bbox': TAIWAN_BBOX,
        'data_range': {
            'start': start_str,
            'end': end_str,
        },
        'overall': {
            'total_detections': summary['total_detections'],
            'dark_vessels': summary['dark_vessels'],
            'dark_ratio': summary['dark_ratio'],
            'dark_by_date': summary['dark_by_date'],
            'total_by_date': summary['total_by_date'],
        },
        'regions': {
            'taiwan_region': summary,
        },
    }

    atomic_write_json(OUTPUT_PATH, output)

    dark_records = build_dark_detection_records(records)
    timestamped = sum(1 for r in dark_records if r.get('timestamp'))
    atomic_write_json(DETECTIONS_PATH, {
        'updated_at': output['updated_at'],
        'data_range': output['data_range'],
        'bbox': TAIWAN_BBOX,
        'total_detections': summary['total_detections'],
        'matched_vessels': summary['matched_vessels'],
        'timestamped_detections': timestamped,
        'dark_detections': dark_records,
    }, compact=True)
    print(f"\n✅ 已儲存全量暗船偵測點: {DETECTIONS_PATH} ({len(dark_records)} 筆，"
          f"{timestamped} 筆帶實際成像時刻)")

    print("\n" + "=" * 70)
    print(f"✅ 已儲存: {OUTPUT_PATH}")
    print(f"   總偵測: {summary['total_detections']}")
    print(f"   暗船: {summary['dark_vessels']} ({summary['dark_ratio']}%)")
    print(f"   資料天數: {len(summary['dark_by_date'])}")
    print("   子區域分布:")
    for zone_id, z in summary['sub_zones'].items():
        print(
            f"     · {zone_id:6s} {z['name']}: "
            f"dark {z['dark_vessels']}/{z['total_detections']} ({z['dark_ratio']}%)"
        )
    print(f"   檔案大小: {OUTPUT_PATH.stat().st_size / 1024:.1f} KB")
    print("=" * 70)


if __name__ == "__main__":
    main()
