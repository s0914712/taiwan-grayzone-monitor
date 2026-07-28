#!/usr/bin/env python3
"""
底圖陸地輪廓產生器 — Taiwan Gray Zone Monitor

把 Natural Earth 1:10m 陸地多邊形裁切到監測海域範圍，輸出成一個精簡的
GeoJSON（`data/land_basemap.geojson`），給 matplotlib 產的靜態地圖當底圖用
（`plot_gov_vessel_tracks.py` 的公務船航跡圖 / 單日海警動態圖）。

原本的地圖只畫了一條手寫的台灣簡化輪廓，中國沿岸完全是空的——停在廈門、
福州錨地的公務船看起來像浮在大海中央。這支腳本補上真實海岸線。

輸出是**committed static asset**（非 CI 產生）：Natural Earth 的世界陸地檔
10MB，且歐亞大陸是單一巨大多邊形，不適合每次 CI 下載，因此在本機裁切一次後
提交。海岸線幾十年才變一次，不需要定期更新。

用法：
    python3 src/build_land_basemap.py                 # 下載並輸出預設檔
    python3 src/build_land_basemap.py --source ne10.geojson -o out.geojson

資料來源：Natural Earth (public domain) 1:10m Physical Vectors — land
    https://github.com/nvkelso/natural-earth-vector
"""
import argparse
import json
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

NE_LAND_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector"
               "/master/geojson/ne_10m_land.geojson")

# 裁切範圍：比 TAIWAN_BBOX (19-30N, 116-130E) 再往外放，
# 讓地圖視野即使超出監測範圍也不會看到被切平的假海岸線
CLIP_BOUNDS = (17.0, 32.0, 114.0, 132.0)  # lat_min, lat_max, lon_min, lon_max

# 座標小數位數（0.001 度 ≈ 100 公尺，靜態地圖綽綽有餘，可大幅縮小檔案）
COORD_PRECISION = 3
# 裁切後點數少於此值的碎片直接丟棄（像素等級的小島，畫不出來只是佔空間）
MIN_RING_POINTS = 4


def _clip_ring(ring, bounds):
    """Sutherland–Hodgman：把一個環裁切到矩形範圍內。

    裁切矩形是凸的，因此對凹多邊形產生的接縫剛好貼在邊界上——而邊界在地圖
    視野之外，看不到。回傳裁切後的點列（可能為空）。
    """
    lat_min, lat_max, lon_min, lon_max = bounds
    # 每個邊界：(取出座標的 index, 界線值, 保留大於還是小於)
    edges = [
        (0, lon_min, True),   # lon >= lon_min
        (0, lon_max, False),  # lon <= lon_max
        (1, lat_min, True),   # lat >= lat_min
        (1, lat_max, False),  # lat <= lat_max
    ]
    poly = list(ring)
    for idx, bound, keep_greater in edges:
        if not poly:
            return []

        def inside(p):
            return p[idx] >= bound if keep_greater else p[idx] <= bound

        out = []
        for cur, prev in zip(poly, poly[-1:] + poly[:-1]):
            cur_in, prev_in = inside(cur), inside(prev)
            if cur_in != prev_in:
                # 線段跨越界線 → 插入交點
                d = cur[idx] - prev[idx]
                t = 0.0 if d == 0 else (bound - prev[idx]) / d
                other = 1 - idx
                pt = [0.0, 0.0]
                pt[idx] = bound
                pt[other] = prev[other] + t * (cur[other] - prev[other])
                out.append(pt)
            if cur_in:
                out.append(list(cur))
        poly = out
    return poly


def _iter_exterior_rings(geojson):
    """走訪所有 (Multi)Polygon 的外環。內環（湖泊）在此尺度無意義，略過。"""
    for feat in geojson.get("features", []):
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if gtype == "Polygon":
            if coords:
                yield coords[0]
        elif gtype == "MultiPolygon":
            for poly in coords:
                if poly:
                    yield poly[0]


def clip_land(geojson, bounds=CLIP_BOUNDS, precision=COORD_PRECISION):
    """裁切世界陸地 GeoJSON → 只含範圍內陸地的精簡 FeatureCollection。"""
    lat_min, lat_max, lon_min, lon_max = bounds
    features = []
    for ring in _iter_exterior_rings(geojson):
        # bbox 預篩：整個環都在範圍外就跳過（省下絕大多數的裁切運算）
        lons = [c[0] for c in ring]
        lats = [c[1] for c in ring]
        if (max(lons) < lon_min or min(lons) > lon_max or
                max(lats) < lat_min or min(lats) > lat_max):
            continue
        clipped = _clip_ring([[c[0], c[1]] for c in ring], bounds)
        if len(clipped) < MIN_RING_POINTS:
            continue
        rounded = [[round(x, precision), round(y, precision)] for x, y in clipped]
        # 去掉裁切／四捨五入後產生的連續重複點
        dedup = [p for i, p in enumerate(rounded) if i == 0 or p != rounded[i - 1]]
        if len(dedup) < MIN_RING_POINTS:
            continue
        if dedup[0] != dedup[-1]:
            dedup.append(dedup[0])
        features.append({
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [dedup]},
        })
    features.sort(key=lambda f: -len(f["geometry"]["coordinates"][0]))
    return {
        "type": "FeatureCollection",
        "properties": {
            "source": "Natural Earth 1:10m physical land (public domain)",
            "clip_bounds": {"lat_min": lat_min, "lat_max": lat_max,
                            "lon_min": lon_min, "lon_max": lon_max},
        },
        "features": features,
    }


def main():
    ap = argparse.ArgumentParser(description="產生靜態地圖用的陸地底圖 GeoJSON")
    ap.add_argument("--source", help="本機 Natural Earth land GeoJSON（預設線上下載）")
    ap.add_argument("-o", "--output", default=str(DATA_DIR / "land_basemap.geojson"))
    args = ap.parse_args()

    if args.source:
        with open(args.source, encoding="utf-8") as f:
            world = json.load(f)
    else:
        print(f"⬇️  下載 Natural Earth 陸地資料…\n   {NE_LAND_URL}")
        with urllib.request.urlopen(NE_LAND_URL, timeout=120) as resp:
            world = json.load(resp)

    clipped = clip_land(world)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(clipped, f, separators=(",", ":"))
    pts = sum(len(feat["geometry"]["coordinates"][0]) for feat in clipped["features"])
    print(f"✅ {out} — {len(clipped['features'])} 塊陸地／{pts} 點／"
          f"{out.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
