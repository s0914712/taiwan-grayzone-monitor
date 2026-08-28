"""縣市界圖產生器的純函式測試（不下載）。

真正的資料來源（geoBoundaries）走 git-lfs，沙箱與 CI 都不該為了跑測試去抓
251KB 的檔案；這裡用合成幾何驗證精簡邏輯，另外把「22 縣市對照表完整」這件事
釘死——少一個 ISO 代碼，前端就會有一塊永遠灰色的縣市。
"""
import json
from pathlib import Path

import build_tw_counties as M

DOCS_GEOJSON = Path(__file__).resolve().parent.parent / "docs" / "tw_counties.geojson"

# 中華民國現行的 22 個直轄市／縣／市
EXPECTED_ISO = {
    "TW-CHA", "TW-CYI", "TW-CYQ", "TW-HSQ", "TW-HSZ", "TW-HUA", "TW-ILA",
    "TW-KEE", "TW-KHH", "TW-KIN", "TW-LIE", "TW-MIA", "TW-NAN", "TW-NWT",
    "TW-PEN", "TW-PIF", "TW-TAO", "TW-TNN", "TW-TPE", "TW-TTT", "TW-TXG",
    "TW-YUN",
}


def test_name_tables_cover_all_22_counties():
    assert set(M.COUNTY_NAMES_ZH) == EXPECTED_ISO
    assert set(M.COUNTY_NAMES_EN) == EXPECTED_ISO


def test_matsu_is_present_which_natural_earth_lacks():
    # 這正是不用 Natural Earth admin-1 的原因（它只有 21 縣市，缺連江）
    assert "馬祖" in M.COUNTY_NAMES_ZH["TW-LIE"]


def test_names_are_traditional_chinese_not_simplified():
    joined = "".join(M.COUNTY_NAMES_ZH.values())
    for simplified in ("桃园", "云林", "台东", "嘉义", "彰化县"):
        assert simplified not in joined


def test_simplify_drops_collinear_points():
    ring = [(0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    out = M.simplify_ring(ring, tolerance=0.002)
    assert (0.5, 0.0) not in out       # 直線上的中間點沒有資訊
    assert out[0] == (0.0, 0.0) and out[-1] == (0.0, 0.0)


def test_simplify_keeps_real_corners():
    ring = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    assert M.simplify_ring(ring, tolerance=0.002) == ring


def test_reduce_ring_closes_and_rounds():
    ring = [(120.123456, 23.123456), (120.5, 23.1), (120.5, 23.5), (120.1, 23.5)]
    out = M.reduce_ring(ring, tolerance=0.0001)
    assert out[0] == out[-1], "GeoJSON 的環必須閉合"
    assert all(len(str(c).split(".")[-1]) <= M.COORD_PRECISION
               for pt in out for c in pt)


def test_reduce_ring_discards_specks():
    speck = [(120.0001, 23.0001), (120.0002, 23.0001), (120.0001, 23.0002),
             (120.0001, 23.0001)]
    assert M.reduce_ring(speck) is None


def test_reduce_geometry_keeps_multipolygon_islands():
    # 澎湖／馬祖是一堆小島：外環被精簡掉的那些要丟，剩下的必須保留
    big = [(119.5, 23.5), (119.7, 23.5), (119.7, 23.7), (119.5, 23.7), (119.5, 23.5)]
    speck = [(119.0, 23.0), (119.0001, 23.0), (119.0, 23.0001), (119.0, 23.0)]
    geom = M.reduce_geometry({"type": "MultiPolygon",
                              "coordinates": [[big], [speck]]})
    assert geom["type"] == "Polygon"       # 只剩一塊時降級成 Polygon
    assert len(geom["coordinates"]) == 1


def test_reduce_geometry_returns_none_for_unsupported_type():
    assert M.reduce_geometry({"type": "Point", "coordinates": [1, 2]}) is None


def test_label_point_uses_largest_island():
    big = [(119.5, 23.5), (119.9, 23.5), (119.9, 23.9), (119.5, 23.9), (119.5, 23.5)]
    small = [(118.0, 26.0), (118.1, 26.0), (118.1, 26.1), (118.0, 26.1), (118.0, 26.0)]
    lat, lon = M.label_point({"type": "MultiPolygon", "coordinates": [[small], [big]]})
    assert 23.4 < lat < 24.0 and 119.4 < lon < 120.0


def test_build_skips_unknown_adm1_and_sorts():
    src = {"features": [
        {"properties": {"shapeISO": "TW-TPE", "shapeName": "Taipei"},
         "geometry": {"type": "Polygon", "coordinates": [[
             (121.5, 25.0), (121.7, 25.0), (121.7, 25.2), (121.5, 25.2),
             (121.5, 25.0)]]}},
        {"properties": {"shapeISO": "CN-35", "shapeName": "Fujian"},
         "geometry": {"type": "Polygon", "coordinates": [[
             (118.0, 25.0), (119.0, 25.0), (119.0, 26.0), (118.0, 25.0)]]}},
    ]}
    out = M.build(src)
    assert [f["properties"]["iso"] for f in out["features"]] == ["TW-TPE"]
    assert out["features"][0]["properties"]["name_zh"] == "臺北市"


def test_committed_geojson_is_complete_and_small():
    """提交進 repo 的那份必須涵蓋 22 縣市，而且不能大到拖垮 Pages。"""
    data = json.loads(DOCS_GEOJSON.read_text(encoding="utf-8"))
    isos = {f["properties"]["iso"] for f in data["features"]}
    assert isos == EXPECTED_ISO
    for feature in data["features"]:
        props = feature["properties"]
        assert props["name_zh"] and props["name_en"]
        assert props["label_point"] and len(props["label_point"]) == 2
    assert DOCS_GEOJSON.stat().st_size < 200 * 1024
