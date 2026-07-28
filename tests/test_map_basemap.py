"""靜態地圖底圖（陸地輪廓 + 海纜圖層）測試。

draw_land / draw_cables 用 stub ax 驗證，因此不需要 matplotlib（CI 未安裝）。
"""
import json

import pytest

import map_basemap
from build_land_basemap import CLIP_BOUNDS, clip_land


class StubAx:
    """記錄 fill/plot 呼叫的假座標軸。"""

    def __init__(self):
        self.fills = []
        self.plots = []

    def fill(self, xs, ys, **kwargs):
        self.fills.append((list(xs), list(ys), kwargs))

    def plot(self, xs, ys, **kwargs):
        self.plots.append((list(xs), list(ys), kwargs))


def _polygon_feature(ring):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


# ── build_land_basemap.clip_land ────────────────────────────────────────────

def test_clip_land_trims_polygon_to_bounds():
    # 一個橫跨整個東亞的大方塊 → 應被裁到 CLIP_BOUNDS 之內
    world = {"features": [_polygon_feature(
        [[100, 0], [150, 0], [150, 45], [100, 45], [100, 0]])]}
    out = clip_land(world)
    assert len(out["features"]) == 1
    ring = out["features"][0]["geometry"]["coordinates"][0]
    lat_min, lat_max, lon_min, lon_max = CLIP_BOUNDS
    assert all(lon_min <= lon <= lon_max and lat_min <= lat <= lat_max
               for lon, lat in ring)
    assert ring[0] == ring[-1]  # 環必須閉合


def test_clip_land_drops_polygons_outside_bounds():
    world = {"features": [_polygon_feature(
        [[-10, 50], [-5, 50], [-5, 55], [-10, 55], [-10, 50]])]}
    assert clip_land(world)["features"] == []


def test_clip_land_keeps_small_island_intact():
    # 金門大小的島：完全在範圍內，不該被裁掉或簡化掉
    ring = [[118.30, 24.40], [118.45, 24.40], [118.45, 24.50],
            [118.30, 24.50], [118.30, 24.40]]
    out = clip_land({"features": [_polygon_feature(ring)]})
    assert len(out["features"]) == 1
    assert len(out["features"][0]["geometry"]["coordinates"][0]) == 5


def test_clip_land_handles_multipolygon():
    world = {"features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "MultiPolygon", "coordinates": [
            [[[120.0, 23.0], [120.5, 23.0], [120.5, 23.5], [120.0, 23.0]]],
            [[[121.0, 24.0], [121.5, 24.0], [121.5, 24.5], [121.0, 24.0]]],
        ]},
    }]}
    assert len(clip_land(world)["features"]) == 2


# ── 已提交的底圖資產 ────────────────────────────────────────────────────────

def test_committed_land_basemap_covers_taiwan_and_china_coast():
    polygons = map_basemap.load_land_polygons()
    assert polygons, "data/land_basemap.geojson 應存在且可解析"

    def covers(lat, lon):
        return any(la_min <= lat <= la_max and lo_min <= lon <= lo_max
                   for _, _, (la_min, la_max, lo_min, lo_max) in polygons)

    assert covers(23.8, 121.0)    # 台灣本島
    assert covers(24.45, 118.35)  # 金門
    assert covers(23.57, 119.62)  # 澎湖
    assert covers(24.48, 118.09)  # 廈門（中國沿岸）


def test_committed_land_basemap_stays_within_clip_bounds():
    lat_min, lat_max, lon_min, lon_max = CLIP_BOUNDS
    for lons, lats, _ in map_basemap.load_land_polygons():
        assert min(lats) >= lat_min - 1e-6 and max(lats) <= lat_max + 1e-6
        assert min(lons) >= lon_min - 1e-6 and max(lons) <= lon_max + 1e-6


# ── 海纜圖層 ────────────────────────────────────────────────────────────────

def test_cable_segments_parse_multilinestring():
    """回歸測試：海纜檔全是 MultiLineString，舊 loader 只認 LineString → 圖層全空。"""
    segments = map_basemap.load_cable_segments()
    assert segments, "應解析出台灣周邊海纜線段"
    lat_min, lat_max, lon_min, lon_max = map_basemap.CABLE_BBOX
    for seg in segments:
        assert len(seg["points"]) >= 2
        for la, lo in seg["points"]:
            assert lat_min <= la <= lat_max and lon_min <= lo <= lon_max


def test_cable_loader_accepts_plain_linestring(tmp_path, monkeypatch):
    f = tmp_path / "cables.json"
    f.write_text(json.dumps({"features": [{
        "properties": {"slug": "test-cable"},
        "geometry": {"type": "LineString",
                     "coordinates": [[121.0, 24.0], [121.5, 24.5]]},
    }]}), encoding="utf-8")
    monkeypatch.setattr(map_basemap, "CABLE_FILES", (f,))
    monkeypatch.setattr(map_basemap, "_CABLE_CACHE", None)
    segments = map_basemap.load_cable_segments()
    assert len(segments) == 1
    assert segments[0]["slug"] == "test-cable"
    assert segments[0]["points"] == [(24.0, 121.0), (24.5, 121.5)]


# ── 繪圖層（stub ax，不需 matplotlib）────────────────────────────────────────

def test_draw_land_only_draws_polygons_in_view():
    ax = StubAx()
    drawn = map_basemap.draw_land(ax, (23.5, 25.5, 120.0, 122.5))  # 台灣附近
    assert drawn == len(ax.fills) > 0

    ax_far = StubAx()
    # 遠離所有陸地的外海方塊 → 一塊都不畫（且不該退回台灣簡化輪廓）
    assert map_basemap.draw_land(ax_far, (20.0, 20.2, 127.0, 127.2)) == 0
    assert ax_far.fills == []


def test_draw_land_falls_back_to_simplified_outline_when_asset_missing(monkeypatch):
    monkeypatch.setattr(map_basemap, "_LAND_CACHE", None)
    monkeypatch.setattr(map_basemap, "LAND_BASEMAP_FILE",
                        map_basemap.BASE_DIR / "data" / "__missing__.geojson")
    ax = StubAx()
    assert map_basemap.draw_land(ax, (22.0, 25.5, 119.0, 122.5)) == 1
    assert len(ax.fills) == 1
    monkeypatch.setattr(map_basemap, "_LAND_CACHE", None)


def test_draw_cables_filters_to_view():
    ax = StubAx()
    drawn = map_basemap.draw_cables(ax, (24.5, 25.5, 121.0, 122.5))  # 北台灣登陸點
    assert drawn == len(ax.plots) > 0
    assert all(kw.get("linestyle") == "--" for _, _, kw in ax.plots)


@pytest.fixture(autouse=True)
def _reset_caches():
    """避免 monkeypatch 過的快取污染其他測試。"""
    yield
    map_basemap._LAND_CACHE = None
    map_basemap._CABLE_CACHE = None
