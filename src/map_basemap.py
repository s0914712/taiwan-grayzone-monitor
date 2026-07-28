#!/usr/bin/env python3
"""
靜態地圖底圖（陸地輪廓）共用模組 — Taiwan Gray Zone Monitor

matplotlib 產的地圖（公務船航跡圖、單日海警動態圖、可疑商船航跡圖）原本只畫
一條手寫的台灣簡化輪廓，中國沿岸是一片空白——停在廈門、福州錨地的船看起來
像浮在大海中央。本模組提供真實海岸線底圖：

  data/land_basemap.geojson（Natural Earth 1:10m 陸地，裁切到監測海域，
  由 src/build_land_basemap.py 產生並提交）

底圖檔缺失時自動退回原本的台灣簡化輪廓，繪圖不會中斷。

另外提供共用的海纜圖層（`load_cable_segments` / `draw_cables`）：海纜 GeoJSON
的 features 全部是 **MultiLineString**，原本 plot_gov_vessel_tracks.py 自己那份
loader 只認 LineString，因此公務船航跡圖的海纜圖層一直是空的（圖例卻有寫）。
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
LAND_BASEMAP_FILE = BASE_DIR / "data" / "land_basemap.geojson"
# 海纜：優先讀完整資料，退回已提交的台灣周邊子集
CABLE_FILES = (BASE_DIR / "data" / "cable-geo.json",
               DOCS_DIR / "taiwan_cables.json")
# 只保留監測海域內的海纜點（與 TAIWAN_BBOX 同範圍略放寬）
CABLE_BBOX = (19.0, 28.0, 115.0, 130.0)  # lat_min, lat_max, lon_min, lon_max

# 暗色主題配色（與前端地圖、既有靜態圖一致）
LAND_FACE = '#1a2640'
LAND_EDGE = '#2a3a5a'

# 底圖檔讀不到時的退路：台灣本島簡化輪廓 (lat, lon)
TAIWAN_COASTLINE = [
    (25.29, 121.57), (25.17, 121.74), (25.03, 121.96),
    (24.98, 121.98), (24.83, 121.84), (24.59, 121.60),
    (24.32, 121.51), (24.08, 121.59), (23.76, 121.48),
    (23.47, 121.35), (23.09, 121.17), (22.76, 121.07),
    (22.52, 120.75), (22.37, 120.59), (22.00, 120.70),
    (22.35, 120.30), (22.59, 120.27), (22.92, 120.26),
    (23.28, 120.18), (23.56, 120.21), (23.93, 120.30),
    (24.25, 120.47), (24.64, 120.68), (24.84, 120.85),
    (25.10, 121.25), (25.29, 121.57),
]

CABLE_COLOR = '#00f5ff'

_LAND_CACHE = None
_CABLE_CACHE = None


def load_land_polygons(path=None):
    """讀取陸地底圖多邊形，回傳 [(lons, lats, (lat_min, lat_max, lon_min, lon_max))]。

    結果快取在模組層級（同一次執行可能畫好幾張圖）。檔案不存在或解析失敗回空 list。
    """
    global _LAND_CACHE
    if path is None and _LAND_CACHE is not None:
        return _LAND_CACHE

    src = Path(path) if path else LAND_BASEMAP_FILE
    polygons = []
    if src.exists():
        try:
            with open(src, encoding='utf-8') as f:
                geo = json.load(f)
            for feat in geo.get('features', []):
                geom = feat.get('geometry') or {}
                if geom.get('type') != 'Polygon':
                    continue
                rings = geom.get('coordinates') or []
                if not rings:
                    continue
                lons = [c[0] for c in rings[0]]  # GeoJSON 為 [lon, lat]
                lats = [c[1] for c in rings[0]]
                if len(lons) < 3:
                    continue
                polygons.append((lons, lats,
                                 (min(lats), max(lats), min(lons), max(lons))))
        except (json.JSONDecodeError, IOError, TypeError) as e:
            print(f"⚠️ 讀取陸地底圖 {src} 失敗，改用簡化輪廓: {e}")
            polygons = []
    else:
        print(f"⚠️ 找不到陸地底圖 {src}，改用台灣簡化輪廓")

    if path is None:
        _LAND_CACHE = polygons
    return polygons


def draw_land(ax, bounds, zorder=1, linewidth=0.7):
    """在 ax 上畫出視野範圍內的陸地。回傳實際畫出的多邊形數。

    bounds = (lat_min, lat_max, lon_min, lon_max)，只畫與視野相交者。
    底圖檔不可用時退回台灣簡化輪廓（維持舊行為）。
    """
    lat_min, lat_max, lon_min, lon_max = bounds
    polygons = load_land_polygons()

    if not polygons:
        ax.fill([p[1] for p in TAIWAN_COASTLINE], [p[0] for p in TAIWAN_COASTLINE],
                facecolor=LAND_FACE, edgecolor=LAND_EDGE, linewidth=1, zorder=zorder)
        return 1

    drawn = 0
    for lons, lats, (p_lat_min, p_lat_max, p_lon_min, p_lon_max) in polygons:
        if (p_lat_max < lat_min or p_lat_min > lat_max or
                p_lon_max < lon_min or p_lon_min > lon_max):
            continue
        ax.fill(lons, lats, facecolor=LAND_FACE, edgecolor=LAND_EDGE,
                linewidth=linewidth, zorder=zorder)
        drawn += 1
    return drawn


def load_cable_segments():
    """載入海纜線段：[{'slug': str, 'points': [(lat, lon), …]}, …]。

    海纜檔的 geometry 皆為 MultiLineString（一條海纜由多段組成），逐段取出並
    裁切到監測海域。結果快取在模組層級。
    """
    global _CABLE_CACHE
    if _CABLE_CACHE is not None:
        return _CABLE_CACHE

    src = next((p for p in CABLE_FILES if p.exists()), None)
    if src is None:
        print("⚠️ 找不到海纜資料，地圖略過海纜圖層")
        _CABLE_CACHE = []
        return _CABLE_CACHE

    lat_min, lat_max, lon_min, lon_max = CABLE_BBOX
    segments = []
    try:
        with open(src, encoding='utf-8') as f:
            geo = json.load(f)
        for feat in geo.get('features', []):
            slug = (feat.get('properties') or {}).get('slug', '')
            geom = feat.get('geometry') or {}
            coords = geom.get('coordinates') or []
            # LineString 只有一層，MultiLineString 多一層 → 統一成線段清單
            lines = [coords] if geom.get('type') == 'LineString' else coords
            for line in lines:
                pts = [(c[1], c[0]) for c in line if len(c) >= 2
                       and lat_min <= c[1] <= lat_max and lon_min <= c[0] <= lon_max]
                if len(pts) >= 2:
                    segments.append({'slug': slug, 'points': pts})
    except (json.JSONDecodeError, IOError, TypeError, IndexError) as e:
        print(f"⚠️ 讀取海纜資料 {src} 失敗: {e}")
        segments = []

    _CABLE_CACHE = segments
    return segments


def draw_cables(ax, bounds, zorder=2, alpha=0.22, linewidth=0.7, margin=0.5):
    """在 ax 上畫出視野範圍內的海纜。回傳實際畫出的線段數。"""
    lat_min, lat_max, lon_min, lon_max = bounds
    drawn = 0
    for cable in load_cable_segments():
        pts = cable['points']
        if not any(lat_min - margin <= la <= lat_max + margin and
                   lon_min - margin <= lo <= lon_max + margin for la, lo in pts):
            continue
        ax.plot([p[1] for p in pts], [p[0] for p in pts], color=CABLE_COLOR,
                alpha=alpha, linewidth=linewidth, linestyle='--', zorder=zorder)
        drawn += 1
    return drawn
