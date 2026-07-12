#!/usr/bin/env python3
"""
================================================================================
Sentinel-1 真實過境時刻擷取 — NASA CMR (Earthdata) granule metadata
================================================================================

match_sar_ais.py 預設用寫死的 Sentinel-1 過境窗（升軌 ≈09:50 UTC、降軌
≈21:55 UTC）估 SAR 成像時刻。本腳本改查 NASA CMR 的 granule metadata，
取得台灣周邊 bbox 內每天「實際」的 Sentinel-1 IW GRD 成像時刻與升降軌，
寫入 data/s1_pass_times.json；match_sar_ais.py 有此檔就用真實時刻，
沒有（或該日期缺資料）就退回固定過境窗。

認證：CMR 的 metadata 搜尋可匿名使用；若設定 EARTHDATA_TOKEN（Earthdata
Login 的 user token / JWT access_token），會以 Authorization: Bearer 帶上
（未來若接需授權的資料集或遇到限流時有用）。token 過期或無效不影響
搜尋 — 本腳本任何失敗都保留既有輸出檔，管線安全降級。

API:
  GET https://cmr.earthdata.nasa.gov/search/granules.umm_json
      ?provider=ASF&platform[]=SENTINEL-1A&...&bounding_box=...&temporal=...

輸出格式 data/s1_pass_times.json：
  {
    "updated_at": ISO8601,
    "source": "cmr",
    "bbox": {...},
    "range": {"start": "...", "end": "..."},
    "passes": {
      "YYYY-MM-DD": [
        {"time": "HH:MM:SS", "direction": "ascending"|"descending",
         "platform": "S1A", "frames": 3}
      ]
    }
  }
================================================================================
"""

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from io_utils import atomic_write_json, load_json, make_retry_session

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
EARTHDATA_TOKEN = os.environ.get('EARTHDATA_TOKEN', '').strip()

DATA_DIR = Path("data")
OUTPUT_PATH = DATA_DIR / 's1_pass_times.json'

# 與 fetch_gfw_data.TAIWAN_BBOX 一致
BBOX = {'lat_min': 15, 'lat_max': 35, 'lon_min': 110, 'lon_max': 128}

RANGE_DAYS = 35          # SAR 資料窗 30 天 + 邊際
PAGE_SIZE = 2000         # CMR 上限
MAX_PAGES = 5
FRESH_HOURS = 12         # 12 小時內抓過就不重抓
PLATFORMS = ('SENTINEL-1A', 'SENTINEL-1B', 'SENTINEL-1C')
PASS_GAP_MIN = 30        # 同日同方向、間隔 ≤30 分鐘的 granule 視為同一次過境


def _headers():
    h = {'Accept': 'application/vnd.nasa.cmr.umm_results+json'}
    if EARTHDATA_TOKEN:
        h['Authorization'] = f'Bearer {EARTHDATA_TOKEN}'
    return h


def fetch_granules(start_iso, end_iso):
    """分頁抓 CMR granule metadata；任何失敗回傳 None（呼叫端保留舊檔）。"""
    session = make_retry_session()
    params = [
        ('provider', 'ASF'),
        ('bounding_box',
         f"{BBOX['lon_min']},{BBOX['lat_min']},{BBOX['lon_max']},{BBOX['lat_max']}"),
        ('temporal', f'{start_iso},{end_iso}'),
        ('page_size', str(PAGE_SIZE)),
        ('sort_key', 'start_date'),
    ]
    for p in PLATFORMS:
        params.append(('platform[]', p))

    items = []
    for page in range(1, MAX_PAGES + 1):
        try:
            resp = session.get(CMR_URL, params=params + [('page_num', str(page))],
                               headers=_headers(), timeout=120)
        except Exception as e:  # requests.RequestException + proxy errors
            print(f"   ❌ CMR 請求失敗: {e}")
            return None
        if resp.status_code != 200:
            print(f"   ❌ CMR 錯誤 {resp.status_code}: {resp.text[:300]}")
            return None
        try:
            batch = resp.json().get('items', [])
        except ValueError as e:
            print(f"   ❌ CMR JSON 解析失敗: {e}")
            return None
        items.extend(batch)
        hits = int(resp.headers.get('CMR-Hits', len(items)))
        if len(items) >= hits or not batch:
            break
    return items


def _granule_fields(item):
    """從一筆 umm_json granule 取 (begin_dt, direction, platform, granule_ur)。
    缺欄位回傳 None。"""
    umm = item.get('umm') or {}
    try:
        begin = umm['TemporalExtent']['RangeDateTime']['BeginningDateTime']
    except (KeyError, TypeError):
        return None
    s = begin.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)

    direction = None
    for attr in umm.get('AdditionalAttributes') or []:
        if attr.get('Name') == 'ASCENDING_DESCENDING':
            vals = attr.get('Values') or []
            if vals:
                direction = str(vals[0]).lower()
            break
    if direction not in ('ascending', 'descending'):
        # 無屬性時以 UTC 時段推斷（台灣經度：升軌上午、降軌晚間）
        direction = 'ascending' if 6 <= dt.hour < 15 else 'descending'

    platforms = umm.get('Platforms') or []
    plat = (platforms[0].get('ShortName', '') if platforms else '') or 'SENTINEL-1'
    plat_short = plat.replace('SENTINEL-1', 'S1')

    granule_ur = umm.get('GranuleUR', '')
    return dt, direction, plat_short, granule_ur


def is_iw_grd(granule_ur):
    """只留 IW 模式 GRD 產品（GFW SAR 偵測的來源）；SLC/RAW/OCN 是同次
    成像的重複計時，排除以免灌高 frame 數。命名不含產品碼時保守保留。"""
    ur = (granule_ur or '').upper()
    if not ur:
        return True
    has_mode = any(m in ur for m in ('_IW_', '_EW_', '_SM_', '_WV_'))
    has_type = any(t in ur for t in ('GRD', 'SLC', 'RAW', 'OCN'))
    if not (has_mode or has_type):
        return True
    return '_IW_' in ur and 'GRD' in ur


def build_pass_table(items):
    """granule 列表 → {date: [pass dict]}。

    同日、同方向、同平台且時間間隔 ≤PASS_GAP_MIN 分鐘的連續 frame
    聚成一次過境，時間取第一個 frame（SAR 掃過台灣 bbox 只需數分鐘）。
    """
    per_key = defaultdict(list)   # (date, direction, platform) -> [dt]
    for item in items:
        f = _granule_fields(item)
        if f is None:
            continue
        dt, direction, plat, ur = f
        if not is_iw_grd(ur):
            continue
        per_key[(dt.strftime('%Y-%m-%d'), direction, plat)].append(dt)

    passes = defaultdict(list)
    for (date, direction, plat), dts in per_key.items():
        dts.sort()
        cluster = [dts[0]]
        for dt in dts[1:]:
            if (dt - cluster[-1]).total_seconds() <= PASS_GAP_MIN * 60:
                cluster.append(dt)
            else:
                passes[date].append(_pass_entry(cluster, direction, plat))
                cluster = [dt]
        passes[date].append(_pass_entry(cluster, direction, plat))

    return {d: sorted(v, key=lambda p: p['time']) for d, v in sorted(passes.items())}


def _pass_entry(cluster, direction, plat):
    return {
        'time': cluster[0].strftime('%H:%M:%S'),
        'direction': direction,
        'platform': plat,
        'frames': len(cluster),
    }


def main():
    print("=" * 70)
    print("🛰️  Sentinel-1 真實過境時刻擷取（NASA CMR / Earthdata）")
    print(f"執行時間: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    print("=" * 70)
    if EARTHDATA_TOKEN:
        print("🔑 使用 EARTHDATA_TOKEN（Bearer）")
    else:
        print("ℹ️ 未設定 EARTHDATA_TOKEN — CMR metadata 搜尋允許匿名，照常進行")

    # 新鮮度檢查（讀檔內 updated_at，不能用 mtime — CI 每次都是新 clone）
    prev = load_json(OUTPUT_PATH, None, expect_type=dict)
    if prev:
        try:
            prev_dt = datetime.fromisoformat(
                (prev.get('updated_at') or '').replace('Z', '+00:00'))
            age_h = (datetime.now(timezone.utc) - prev_dt).total_seconds() / 3600
            if age_h < FRESH_HOURS:
                print(f"⏭️ {OUTPUT_PATH} 尚新（{age_h:.1f}h），跳過")
                return 0
        except ValueError:
            pass

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=RANGE_DAYS)
    start_iso = start.strftime('%Y-%m-%dT00:00:00Z')
    end_iso = end.strftime('%Y-%m-%dT%H:%M:%SZ')
    print(f"📅 查詢範圍: {start_iso} ~ {end_iso}")

    items = fetch_granules(start_iso, end_iso)
    if items is None:
        print("❌ CMR 查詢失敗，保留既有 s1_pass_times.json（不覆寫）")
        return 0
    print(f"   取得 {len(items)} 筆 granule metadata")

    passes = build_pass_table(items)
    if not passes:
        print("⚠️ 沒有解析出任何過境，保留既有檔案（不覆寫）")
        return 0

    output = {
        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'source': 'cmr',
        'bbox': BBOX,
        'range': {'start': start_iso[:10], 'end': end_iso[:10]},
        'passes': passes,
    }
    atomic_write_json(OUTPUT_PATH, output)

    n_pass = sum(len(v) for v in passes.values())
    print("\n" + "=" * 70)
    print(f"✅ 已儲存: {OUTPUT_PATH}")
    print(f"   涵蓋日數: {len(passes)}、過境次數: {n_pass}")
    for date in list(passes)[-3:]:
        desc = ', '.join(f"{p['platform']} {p['direction'][:4]} {p['time']}"
                         for p in passes[date])
        print(f"     · {date}: {desc}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
