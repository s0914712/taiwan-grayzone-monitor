"""海域法域分類與海纜緩衝區 — Maritime zone classification & cable buffers.

提供把單一座標（或一批座標）對應到：
  * 海域法域：內水 / 領海(12浬) / 鄰接區(24浬) / 經濟海域(≤200浬) / 公海
    — 以內政部公告的領海基線（docs/data/territorial_baseline.json）量測。
  * 最近海底電纜距離與緩衝帶：≤1km / ≤5km / ≤10km / >10km
    — 以 data/cable-geo.json 的線段量測。

設計為純 stdlib（update-ais.yml 只裝 requests + pysocks），幾何運算復用
geo_utils。幾何資料以惰性單例載入；資料檔缺失時各函式安全降級（回傳 unknown）。

CLI：``python src/geofence.py <lat> <lon>`` 印出單點分類，便於測試/示範。

注意：此處 EEZ 採「距基線 ≤200 浬」之簡化定義，未做中線劃界，僅供風險研判
參考，非法律上的專屬經濟海域界線。
"""
import json
import sys
from pathlib import Path

from geo_utils import (
    haversine_km, point_in_polygon, distance_to_polyline_km,
    point_to_segment_distance_km, km_to_nm, nm_to_km,
)

_REPO = Path(__file__).resolve().parent.parent
BASELINE_FILE = _REPO / "docs" / "data" / "territorial_baseline.json"
CABLE_GEO_FILE = _REPO / "data" / "cable-geo.json"

# 法域距離門檻（浬）
TERRITORIAL_SEA_NM = 12.0
CONTIGUOUS_ZONE_NM = 24.0
EEZ_NM = 200.0

# 海纜緩衝帶（公里）
CABLE_BANDS_KM = (1.0, 5.0, 10.0)

# 只保留台灣周邊海域的海纜線段（與 analyze_suspicious 一致）
_CABLE_BBOX = (19, 28, 115, 130)  # lat_min, lat_max, lon_min, lon_max

_baselines = None      # list[list[(lat, lon)]]
_cable_segments = None  # list[dict(points, bbox)]


# ── 資料載入 ────────────────────────────────────────────────────────────────
def load_baselines():
    """載入領海基線多邊形，回傳 list of polygons（每個為 [(lat, lon), ...]）。"""
    global _baselines
    if _baselines is not None:
        return _baselines
    _baselines = []
    if not BASELINE_FILE.exists():
        return _baselines
    try:
        data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _baselines
    for region, pts in data.items():
        poly = [(p[1], p[0]) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]
        if len(poly) >= 3:
            _baselines.append(poly)
    return _baselines


def load_cable_segments():
    """載入台灣周邊海纜線段，回傳 list of dict(points=[(lat,lon)...], bbox)。"""
    global _cable_segments
    if _cable_segments is not None:
        return _cable_segments
    _cable_segments = []
    if not CABLE_GEO_FILE.exists():
        return _cable_segments
    try:
        data = json.loads(CABLE_GEO_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _cable_segments
    la0, la1, lo0, lo1 = _CABLE_BBOX
    for feat in data.get("features", []):
        for segment in feat.get("geometry", {}).get("coordinates", []):
            pts = [(lat, lon) for lon, lat in segment
                   if la0 <= lat <= la1 and lo0 <= lon <= lo1]
            if len(pts) >= 2:
                lats = [p[0] for p in pts]
                lons = [p[1] for p in pts]
                _cable_segments.append({
                    "points": pts,
                    "bbox": (min(lats), min(lons), max(lats), max(lons)),
                })
    return _cable_segments


# ── 分類函式 ────────────────────────────────────────────────────────────────
def classify_maritime_zone(lat, lon, baselines=None):
    """把座標分類到海域法域。

    回傳 dict：
        {"zone": <str>, "distance_to_baseline_nm": <float|None>,
         "distance_to_baseline_km": <float|None>, "inside_baseline": <bool>}
    zone ∈ internal_waters / territorial_sea / contiguous_zone / eez /
            high_seas / unknown
    """
    if baselines is None:
        baselines = load_baselines()
    if not baselines:
        return {"zone": "unknown", "distance_to_baseline_nm": None,
                "distance_to_baseline_km": None, "inside_baseline": False}

    if any(point_in_polygon(lat, lon, poly) for poly in baselines):
        return {"zone": "internal_waters", "distance_to_baseline_nm": 0.0,
                "distance_to_baseline_km": 0.0, "inside_baseline": True}

    dists = [distance_to_polyline_km(lat, lon, poly, closed=True) for poly in baselines]
    dists = [d for d in dists if d is not None]
    if not dists:
        return {"zone": "unknown", "distance_to_baseline_nm": None,
                "distance_to_baseline_km": None, "inside_baseline": False}
    d_km = min(dists)
    d_nm = km_to_nm(d_km)
    if d_nm <= TERRITORIAL_SEA_NM:
        zone = "territorial_sea"
    elif d_nm <= CONTIGUOUS_ZONE_NM:
        zone = "contiguous_zone"
    elif d_nm <= EEZ_NM:
        zone = "eez"
    else:
        zone = "high_seas"
    return {"zone": zone, "distance_to_baseline_nm": round(d_nm, 2),
            "distance_to_baseline_km": round(d_km, 2), "inside_baseline": False}


def nearest_cable(lat, lon, segments=None, max_band_km=None):
    """最近海底電纜距離與緩衝帶。

    回傳 dict：{"nearest_cable_km": <float|None>, "cable_band": <str>}
    cable_band ∈ within_1km / within_5km / within_10km / beyond_10km / unknown
    max_band_km 可加速：超過此距離的線段以 bbox 預先排除（預設取最大緩衝帶）。
    """
    if segments is None:
        segments = load_cable_segments()
    if not segments:
        return {"nearest_cable_km": None, "cable_band": "unknown"}

    best = None
    # bbox 預過濾：經緯度 1° 上限約 111 km，用較寬鬆的角度緩衝。
    prefilter = None
    if max_band_km is not None:
        prefilter = max_band_km / 100.0  # ~1.1km/0.01°，寬鬆即可
    for seg in segments:
        if prefilter is not None and best is not None:
            la0, lo0, la1, lo1 = seg["bbox"]
            if (lat < la0 - prefilter or lat > la1 + prefilter or
                    lon < lo0 - prefilter or lon > lo1 + prefilter):
                continue
        pts = seg["points"]
        for i in range(len(pts) - 1):
            d = point_to_segment_distance_km(lat, lon,
                                             pts[i][0], pts[i][1],
                                             pts[i + 1][0], pts[i + 1][1])
            if best is None or d < best:
                best = d
    if best is None:
        return {"nearest_cable_km": None, "cable_band": "unknown"}
    if best <= CABLE_BANDS_KM[0]:
        band = "within_1km"
    elif best <= CABLE_BANDS_KM[1]:
        band = "within_5km"
    elif best <= CABLE_BANDS_KM[2]:
        band = "within_10km"
    else:
        band = "beyond_10km"
    return {"nearest_cable_km": round(best, 2), "cable_band": band}


def annotate(lat, lon):
    """組合單點的法域 + 海纜緩衝帶標註。"""
    out = classify_maritime_zone(lat, lon)
    out.update(nearest_cable(lat, lon))
    return out


def _main(argv):
    if len(argv) != 2:
        print("usage: python src/geofence.py <lat> <lon>")
        return 1
    lat, lon = float(argv[0]), float(argv[1])
    result = annotate(lat, lon)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
