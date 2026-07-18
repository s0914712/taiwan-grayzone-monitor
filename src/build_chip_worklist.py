#!/usr/bin/env python3
"""
================================================================================
SAR 取證清單 — Chip-retrieval worklist for residual dark detections
================================================================================

fetch_sar_chip.py 需要「座標＋日期＋成像時刻」，但 Sentinel-1 不是每天過境
（台灣東部外海更是 IW 模式覆蓋薄區），人工逐日試查很沒效率。本腳本在 CI
（網路不受限）預先做好功課：

1. 讀 match_sar_ais.py 的殘餘暗船（residual_dark）
2. 篩出關注海域（預設：台灣東部 + 西南海域，WORKLIST_ZONES 可調）
3. 向 CDSE OData 目錄（匿名）一次抓回資料窗內所有 IW GRDH 產品的
   足跡（GeoFootprint）
4. 逐筆判斷偵測點落在哪一景產品足跡內（point-in-polygon）
5. 輸出 data/sar_chip_worklist.json：每筆目標附上涵蓋產品、確切成像
   時刻、以及可直接複製執行的 fetch_sar_chip.py 指令；查無涵蓋的
   也標出來（省得使用者手動試）

輸出由 generate_dashboard.py 複製到 docs/，報告頁（sar-ais-match.html）
的「取證清單」面板直接顯示。
================================================================================
"""

import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from geo_utils import point_in_polygon
from io_utils import atomic_write_json, load_json, make_retry_session

CDSE_ODATA_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"

DATA_DIR = Path("data")
MATCHES_PATH = DATA_DIR / 'sar_ais_matches.json'
S1_PASS_TIMES_PATH = DATA_DIR / 's1_pass_times.json'
OUTPUT_PATH = DATA_DIR / 'sar_chip_worklist.json'

# 關注海域（使用者指定：台灣東部 + 西南）— 調整這裡即可增減
WORKLIST_ZONES = {
    'east': {
        'name': '台灣東部海域', 'name_en': 'East of Taiwan',
        'lat_min': 21.8, 'lat_max': 25.5, 'lon_min': 121.5, 'lon_max': 124.5,
    },
    'southwest': {
        'name': '台灣西南海域', 'name_en': 'Southwest of Taiwan',
        'lat_min': 20.0, 'lat_max': 23.5, 'lon_min': 117.0, 'lon_max': 120.8,
    },
}

MAX_TARGETS = 120        # 清單上限（最新優先）
ODATA_PAGE = 1000
ODATA_MAX_PAGES = 5


# =============================================================================
# 純函式
# =============================================================================

def zone_of(lat, lon, zones=WORKLIST_ZONES):
    for zone_id, z in zones.items():
        if z['lat_min'] <= lat <= z['lat_max'] and \
                z['lon_min'] <= lon <= z['lon_max']:
            return zone_id
    return None


def footprint_polygons(geo_footprint):
    """OData GeoFootprint（GeoJSON Polygon / MultiPolygon）→
    list of [(lat, lon), ...]（外環）。無法解析回傳空 list。"""
    if not isinstance(geo_footprint, dict):
        return []
    gtype = geo_footprint.get('type')
    coords = geo_footprint.get('coordinates')
    if not coords:
        return []
    if gtype == 'Polygon':
        rings = [coords]
    elif gtype == 'MultiPolygon':
        rings = coords
    else:
        return []
    out = []
    for poly in rings:
        try:
            ring = [(float(pt[1]), float(pt[0])) for pt in poly[0]]
        except (TypeError, ValueError, IndexError):
            continue
        if len(ring) >= 3:
            out.append(ring)
    return out


def product_covers(product, lat, lon):
    return any(point_in_polygon(lat, lon, ring)
               for ring in product.get('_rings', []))


def parse_start(product):
    s = ((product.get('ContentDate') or {}).get('Start') or '')
    s = s.replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except ValueError:
        return None


def prepare_products(items):
    """OData 產品 → [{name, date, time, _rings}]，僅保留可解析者。"""
    out = []
    for p in items or []:
        dt = parse_start(p)
        name = p.get('Name') or ''
        if dt is None or not name:
            continue
        rings = footprint_polygons(p.get('GeoFootprint'))
        if not rings:
            continue
        out.append({
            'name': name,
            'date': dt.strftime('%Y-%m-%d'),
            'time': dt.strftime('%H:%M'),
            'time_full': dt.strftime('%H:%M:%S'),
            '_rings': rings,
        })
    return out


def build_command(lat, lon, date, time_hhmm):
    cmd = f"python fetch_sar_chip.py {lat} {lon} {date}"
    if time_hhmm:
        cmd += f" --time {time_hhmm}"
    return cmd


def build_worklist(residual, products, zones=WORKLIST_ZONES,
                   max_targets=MAX_TARGETS):
    """殘餘暗船 × 產品足跡 → 取證清單。

    回傳 (targets, zone_counts)。targets 每筆：
      {lat, lon, date, zone, maritime_zone, in_ais_coverage,
       covered, product, time, command}
    """
    by_date = defaultdict(list)
    for p in products:
        by_date[p['date']].append(p)

    targets = []
    zone_counts = defaultdict(int)
    for r in residual or []:
        lat, lon, date = r.get('lat'), r.get('lon'), r.get('date')
        if lat is None or lon is None or not date:
            continue
        zone_id = zone_of(lat, lon, zones)
        if zone_id is None:
            continue
        zone_counts[zone_id] += 1
        covering = [p for p in by_date.get(date, ())
                    if product_covers(p, lat, lon)]
        entry = {
            'lat': lat, 'lon': lon, 'date': date,
            'zone': zone_id,
            'maritime_zone': r.get('zone', 'unknown'),
            'in_ais_coverage': bool(r.get('in_ais_coverage', True)),
            'covered': bool(covering),
        }
        if covering:
            covering.sort(key=lambda p: p['time'])
            best = covering[0]
            entry['product'] = best['name']
            entry['time'] = best['time_full']
            entry['command'] = build_command(lat, lon, date, best['time'])
            if len(covering) > 1:
                entry['alt_times'] = [p['time_full'] for p in covering[1:]]
        else:
            entry['command'] = None
        targets.append(entry)

    # 最新日期優先；同日期內「有涵蓋」優先
    targets.sort(key=lambda t: (t['date'], t['covered']), reverse=True)
    return targets[:max_targets], dict(zone_counts)


# =============================================================================
# CDSE OData
# =============================================================================

def fetch_products(date_min, date_max, bbox):
    """抓資料窗內、bbox 相交的所有 IW GRDH 產品（含足跡）；失敗回傳 None。"""
    session = make_retry_session()
    wkt = (f"POLYGON(({bbox['lon_min']} {bbox['lat_min']},"
           f"{bbox['lon_max']} {bbox['lat_min']},"
           f"{bbox['lon_max']} {bbox['lat_max']},"
           f"{bbox['lon_min']} {bbox['lat_max']},"
           f"{bbox['lon_min']} {bbox['lat_min']}))")
    filt = (
        "Collection/Name eq 'SENTINEL-1'"
        " and contains(Name,'_IW_GRDH_')"
        f" and OData.CSC.Intersects(area=geography'SRID=4326;{wkt}')"
        f" and ContentDate/Start ge {date_min}T00:00:00.000Z"
        f" and ContentDate/Start le {date_max}T23:59:59.999Z"
    )
    items = []
    skip = 0
    while len(items) < ODATA_PAGE * ODATA_MAX_PAGES:
        try:
            resp = session.get(CDSE_ODATA_URL, params={
                '$filter': filt,
                '$top': str(ODATA_PAGE),
                '$skip': str(skip),
                '$orderby': 'ContentDate/Start asc',
            }, timeout=120)
        except Exception as e:
            print(f"   ❌ CDSE OData 請求失敗: {e}")
            return None
        if resp.status_code != 200:
            print(f"   ❌ CDSE OData 錯誤 {resp.status_code}: {resp.text[:300]}")
            return None
        try:
            batch = resp.json().get('value', [])
        except ValueError as e:
            print(f"   ❌ CDSE JSON 解析失敗: {e}")
            return None
        items.extend(batch)
        if len(batch) < ODATA_PAGE:
            break
        skip += ODATA_PAGE
    return items


def zones_bbox(zones=WORKLIST_ZONES):
    return {
        'lat_min': min(z['lat_min'] for z in zones.values()),
        'lat_max': max(z['lat_max'] for z in zones.values()),
        'lon_min': min(z['lon_min'] for z in zones.values()),
        'lon_max': max(z['lon_max'] for z in zones.values()),
    }


# =============================================================================
# 主程式
# =============================================================================

def main():
    print("=" * 70)
    print("🧰 SAR 取證清單（東部＋西南殘餘暗船 × Sentinel-1 產品涵蓋）")
    print(f"執行時間: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    print("=" * 70)

    matches = load_json(MATCHES_PATH, None, expect_type=dict)
    residual = (matches or {}).get('residual_dark') or []
    if not residual:
        print("⚠️ 沒有殘餘暗船資料（先跑 match_sar_ais.py），跳過")
        return 0

    in_zone = [r for r in residual
               if r.get('lat') is not None and r.get('lon') is not None
               and zone_of(r['lat'], r['lon']) is not None]
    print(f"🔦 殘餘暗船 {len(residual)} 筆，關注海域內 {len(in_zone)} 筆")
    if not in_zone:
        print("⚠️ 關注海域內沒有目標，輸出空清單")

    dates = sorted({r['date'] for r in in_zone if r.get('date')})
    date_min = dates[0] if dates else datetime.now(timezone.utc).strftime('%Y-%m-%d')
    date_max = dates[-1] if dates else date_min

    items = fetch_products(date_min, date_max, zones_bbox()) if in_zone else []
    if items is None:
        print("❌ 目錄查詢失敗，保留既有清單（不覆寫）")
        return 0
    products = prepare_products(items)
    print(f"🛰️ 資料窗 {date_min}~{date_max} 內取得 {len(products)} 景 IW GRDH 產品足跡")

    targets, zone_counts = build_worklist(in_zone, products)
    covered = sum(1 for t in targets if t['covered'])

    output = {
        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'zones': {zid: {k: v for k, v in z.items()}
                  for zid, z in WORKLIST_ZONES.items()},
        'summary': {
            'residual_total': len(residual),
            'in_zone': len(in_zone),
            'listed': len(targets),
            'covered': covered,
            'no_coverage': len(targets) - covered,
            'by_zone': zone_counts,
            'products_indexed': len(products),
            'date_range': {'start': date_min, 'end': date_max},
        },
        'targets': targets,
    }
    atomic_write_json(OUTPUT_PATH, output)

    print("\n" + "=" * 70)
    print(f"✅ 已儲存: {OUTPUT_PATH}")
    print(f"   清單目標: {len(targets)}（有影像涵蓋 {covered}、無涵蓋 {len(targets) - covered}）")
    for t in targets[:5]:
        mark = '🎯' if t['covered'] else '⬜'
        print(f"     {mark} {t['date']} ({t['lat']}, {t['lon']}) "
              f"{t.get('time', 'no coverage')}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
