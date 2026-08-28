#!/usr/bin/env python3
"""
台灣縣市界圖產生器 — Taiwan Gray Zone Monitor

把 geoBoundaries 的台灣 ADM1（22 縣市）界線精簡成前端可用的 GeoJSON
（`docs/tw_counties.geojson`），給 `network-traffic.html` 的縣市網速／連線狀態
色塊地圖當底圖用。

**為什麼是 geoBoundaries 而不是 Natural Earth**：本專案的靜態地圖底圖走 Natural
Earth（`build_land_basemap.py`），但 NE 10m 的 admin-1 只有 **21** 個台灣縣市 ——
**缺連江縣（馬祖）**，而馬祖正是這個專案最關鍵的一塊（2023 年 2 月兩條海纜先後
被弄斷、靠微波備援撐了數週）。NE 的 `name_zh` 又是簡體（「桃园市」），對 zh-TW
的站台也不能直接用。geoBoundaries gbOpen TWN ADM1 有完整 22 縣市（含 TW-LIE）
且帶 ISO 3166-2 代碼，因此中文名在本檔手寫一份 zh-TW 對照表。

輸出是 **committed static asset**（非 CI 產生）：縣市界幾年才變一次，沒有理由每
2 小時的資料管線都下載一次。

用法：
    python3 src/build_tw_counties.py                  # 下載並輸出預設檔
    python3 src/build_tw_counties.py --source gb.geojson -o out.geojson

資料來源：geoBoundaries (CC BY 4.0), gbOpen TWN ADM1
    https://www.geoboundaries.org/
"""
import argparse
import json
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"

# 注意：這個檔在 GitHub 上走 git-lfs，raw.githubusercontent 只會回一個 LFS
# pointer（130 bytes 的文字檔）。必須走 media.githubusercontent 才拿得到內容。
GB_URL = ("https://media.githubusercontent.com/media/wmgeolab/geoBoundaries"
          "/main/releaseData/gbOpen/TWN/ADM1"
          "/geoBoundaries-TWN-ADM1_simplified.geojson")

# 座標小數位數（0.001 度 ≈ 100 公尺）；先 Douglas–Peucker 再四捨五入，
# 純四捨五入會在直線上留下大量重複點。
COORD_PRECISION = 3
# Douglas–Peucker 容差（度）。0.002° ≈ 200m：縣市界在全台視野下看不出差別，
# 但檔案小一半以上。
SIMPLIFY_TOLERANCE = 0.002
# 精簡後點數不足以構成多邊形的環直接丟棄（離島的小礁岩，畫出來只有一個像素）
MIN_RING_POINTS = 4

# ISO 3166-2:TW → 正體中文縣市名。geoBoundaries 只給英文名，且英文名不一致
# （"Taipei" vs "Hsinchu County"），因此以 ISO 代碼為 key。
COUNTY_NAMES_ZH = {
    "TW-CHA": "彰化縣", "TW-CYI": "嘉義市", "TW-CYQ": "嘉義縣",
    "TW-HSQ": "新竹縣", "TW-HSZ": "新竹市", "TW-HUA": "花蓮縣",
    "TW-ILA": "宜蘭縣", "TW-KEE": "基隆市", "TW-KHH": "高雄市",
    "TW-KIN": "金門縣", "TW-LIE": "連江縣（馬祖）", "TW-MIA": "苗栗縣",
    "TW-NAN": "南投縣", "TW-NWT": "新北市", "TW-PEN": "澎湖縣",
    "TW-PIF": "屏東縣", "TW-TAO": "桃園市", "TW-TNN": "臺南市",
    "TW-TPE": "臺北市", "TW-TTT": "臺東縣", "TW-TXG": "臺中市",
    "TW-YUN": "雲林縣",
}

# 英文名也統一過（geoBoundaries 的 "Matsu Islands"／"New Taipei" 混用單複數與
# 有無 County，前端排版看起來會很亂）
COUNTY_NAMES_EN = {
    "TW-CHA": "Changhua", "TW-CYI": "Chiayi City", "TW-CYQ": "Chiayi County",
    "TW-HSQ": "Hsinchu County", "TW-HSZ": "Hsinchu City", "TW-HUA": "Hualien",
    "TW-ILA": "Yilan", "TW-KEE": "Keelung", "TW-KHH": "Kaohsiung",
    "TW-KIN": "Kinmen", "TW-LIE": "Lienchiang (Matsu)", "TW-MIA": "Miaoli",
    "TW-NAN": "Nantou", "TW-NWT": "New Taipei", "TW-PEN": "Penghu",
    "TW-PIF": "Pingtung", "TW-TAO": "Taoyuan", "TW-TNN": "Tainan",
    "TW-TPE": "Taipei", "TW-TTT": "Taitung", "TW-TXG": "Taichung",
    "TW-YUN": "Yunlin",
}


def _perp_distance(pt, start, end):
    """點到線段的垂直距離（以度為單位的平面近似）。

    只用來決定「這個點能不能省」，台灣範圍內經緯度的尺度差（cos24° ≈ 0.91）
    對取捨的影響遠小於容差本身，不值得為此做投影。
    """
    (x, y), (x0, y0), (x1, y1) = pt, start, end
    dx, dy = x1 - x0, y1 - y0
    if dx == 0 and dy == 0:
        return ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5
    return abs(dy * x - dx * y + x1 * y0 - y1 * x0) / ((dx * dx + dy * dy) ** 0.5)


def simplify_ring(ring, tolerance=SIMPLIFY_TOLERANCE):
    """Douglas–Peucker（迭代版，避免深遞迴炸掉大環）。"""
    if len(ring) < 3:
        return list(ring)
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        worst_d, worst_i = -1.0, None
        for i in range(first + 1, last):
            d = _perp_distance(ring[i], ring[first], ring[last])
            if d > worst_d:
                worst_d, worst_i = d, i
        if worst_d > tolerance:
            keep[worst_i] = True
            stack.append((first, worst_i))
            stack.append((worst_i, last))
    return [pt for pt, k in zip(ring, keep) if k]


def reduce_ring(ring, tolerance=SIMPLIFY_TOLERANCE, precision=COORD_PRECISION):
    """精簡 + 降精度 + 去掉連續重複點；點數不足回傳 None（呼叫端丟棄）。"""
    simplified = simplify_ring(ring, tolerance)
    out = []
    for lon, lat in simplified:
        pt = [round(lon, precision), round(lat, precision)]
        if not out or out[-1] != pt:
            out.append(pt)
    if len(out) >= 3 and out[0] != out[-1]:
        out.append(list(out[0]))   # GeoJSON 的環必須閉合
    if len(out) < MIN_RING_POINTS:
        return None
    return out


def reduce_geometry(geometry, tolerance=SIMPLIFY_TOLERANCE,
                    precision=COORD_PRECISION):
    """對 Polygon／MultiPolygon 逐環精簡。整個 geometry 都被精簡掉時回 None。"""
    gtype = geometry.get("type")
    if gtype == "Polygon":
        polys = [geometry.get("coordinates") or []]
    elif gtype == "MultiPolygon":
        polys = geometry.get("coordinates") or []
    else:
        return None

    out_polys = []
    for poly in polys:
        rings = []
        for idx, ring in enumerate(poly):
            reduced = reduce_ring(ring, tolerance, precision)
            if reduced is None:
                if idx == 0:
                    rings = []       # 外環沒了，內環也沒有意義
                    break
                continue             # 洞太小，忽略
            rings.append(reduced)
        if rings:
            out_polys.append(rings)
    if not out_polys:
        return None
    if len(out_polys) == 1:
        return {"type": "Polygon", "coordinates": out_polys[0]}
    return {"type": "MultiPolygon", "coordinates": out_polys}


def ring_area(ring):
    """鞋帶公式的絕對面積（度²），只用來挑「最大的那塊」。"""
    total = 0.0
    for i in range(len(ring) - 1):
        x0, y0 = ring[i]
        x1, y1 = ring[i + 1]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def label_point(geometry):
    """標籤位置：取面積最大的外環的形心（[lat, lon]）。

    小島散布的縣市（澎湖、馬祖）若用全部環的平均，標籤會掉在海裡；取最大島的
    形心至少落在陸地上。
    """
    if geometry["type"] == "Polygon":
        polys = [geometry["coordinates"]]
    else:
        polys = geometry["coordinates"]
    best, best_area = None, -1.0
    for poly in polys:
        outer = poly[0]
        area = ring_area(outer)
        if area > best_area:
            best_area, best = area, outer
    if not best:
        return None
    lons = [p[0] for p in best]
    lats = [p[1] for p in best]
    return [round(sum(lats) / len(lats), 4), round(sum(lons) / len(lons), 4)]


def build(source_geojson, tolerance=SIMPLIFY_TOLERANCE,
          precision=COORD_PRECISION):
    """geoBoundaries FeatureCollection → 精簡過的縣市 FeatureCollection。"""
    features = []
    for feat in source_geojson.get("features") or []:
        props = feat.get("properties") or {}
        iso = (props.get("shapeISO") or "").strip().upper()
        if iso not in COUNTY_NAMES_ZH:
            print(f"⚠️ 略過未知的 ADM1：{props.get('shapeName')} / {iso!r}")
            continue
        geometry = reduce_geometry(feat.get("geometry") or {}, tolerance, precision)
        if geometry is None:
            print(f"⚠️ {iso} 精簡後沒有可用幾何，略過")
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "iso": iso,
                "name_zh": COUNTY_NAMES_ZH[iso],
                "name_en": COUNTY_NAMES_EN[iso],
                "label_point": label_point(geometry),
            },
            "geometry": geometry,
        })
    features.sort(key=lambda f: f["properties"]["iso"])
    missing = sorted(set(COUNTY_NAMES_ZH) - {f["properties"]["iso"] for f in features})
    if missing:
        print(f"⚠️ 來源缺少縣市：{missing}")
    return {
        "type": "FeatureCollection",
        "source": "geoBoundaries gbOpen TWN ADM1 (CC BY 4.0)",
        "simplify_tolerance_deg": tolerance,
        "features": features,
    }


def main():
    ap = argparse.ArgumentParser(description="產生台灣縣市界 GeoJSON")
    ap.add_argument("--source", help="本機 geoBoundaries GeoJSON（省略則下載）")
    ap.add_argument("--tolerance", type=float, default=SIMPLIFY_TOLERANCE)
    ap.add_argument("-o", "--output", default=str(DOCS_DIR / "tw_counties.geojson"))
    args = ap.parse_args()

    if args.source:
        with open(args.source, encoding="utf-8") as f:
            src = json.load(f)
    else:
        print(f"⬇️  下載 {GB_URL}")
        with urllib.request.urlopen(GB_URL, timeout=180) as resp:
            src = json.loads(resp.read().decode("utf-8"))
    print(f"   ↳ 來源 {len(src.get('features') or [])} 個 feature")

    out = build(src, tolerance=args.tolerance)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = path.stat().st_size / 1024
    print(f"✅ {path}  {len(out['features'])} 縣市｜{size_kb:.0f} KB")


if __name__ == "__main__":
    main()
