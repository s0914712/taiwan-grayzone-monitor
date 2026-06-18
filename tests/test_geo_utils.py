import math

import pytest

from geo_utils import haversine_km, calc_bearing


def test_haversine_zero_distance():
    assert haversine_km(24.0, 121.0, 24.0, 121.0) == 0.0


def test_haversine_one_degree_lat():
    # 緯度 1° ≈ 111.19 km（與經度無關）
    assert haversine_km(24.0, 121.0, 25.0, 121.0) == pytest.approx(111.19, abs=0.5)


def test_haversine_taipei_kaohsiung():
    # 台北 (25.03, 121.56) ↔ 高雄 (22.62, 120.31) ≈ 296 km
    d = haversine_km(25.03, 121.56, 22.62, 120.31)
    assert d == pytest.approx(296, abs=10)


def test_haversine_symmetry():
    a = haversine_km(24.5, 120.2, 26.1, 122.7)
    b = haversine_km(26.1, 122.7, 24.5, 120.2)
    assert a == pytest.approx(b, rel=1e-12)


def test_bearing_cardinal_directions():
    assert calc_bearing(24.0, 121.0, 25.0, 121.0) == pytest.approx(0, abs=0.01)    # 正北
    assert calc_bearing(24.0, 121.0, 23.0, 121.0) == pytest.approx(180, abs=0.01)  # 正南
    assert calc_bearing(0.0, 121.0, 0.0, 122.0) == pytest.approx(90, abs=0.01)     # 赤道正東
    assert calc_bearing(0.0, 121.0, 0.0, 120.0) == pytest.approx(270, abs=0.01)    # 赤道正西


def test_bearing_range():
    for dlat, dlon in [(1, 1), (-1, 1), (-1, -1), (1, -1)]:
        b = calc_bearing(24.0, 121.0, 24.0 + dlat, 121.0 + dlon)
        assert 0 <= b < 360


# ── Geometry primitives (geofence support) ──────────────────────────────────
from geo_utils import (  # noqa: E402
    NM_TO_KM, km_to_nm, nm_to_km,
    point_to_segment_distance_km, distance_to_polyline_km, point_in_polygon,
)


def test_nm_conversions_roundtrip():
    assert nm_to_km(12) == pytest.approx(22.224, abs=1e-3)
    assert km_to_nm(NM_TO_KM) == pytest.approx(1.0, abs=1e-9)
    assert km_to_nm(nm_to_km(24)) == pytest.approx(24, rel=1e-12)


def test_point_to_segment_on_segment_is_zero():
    # point exactly on the segment midpoint
    d = point_to_segment_distance_km(24.0, 121.5, 24.0, 121.0, 24.0, 122.0)
    assert d == pytest.approx(0, abs=1e-6)


def test_point_to_segment_endpoint_projection():
    # point west of the segment start projects onto the start endpoint
    d = point_to_segment_distance_km(24.0, 120.0, 24.0, 121.0, 24.0, 122.0)
    assert d == pytest.approx(haversine_km(24.0, 120.0, 24.0, 121.0), rel=1e-9)


def test_distance_to_polyline_open_vs_closed():
    # square ring (lat, lon) corners
    sq = [(0, 0), (0, 1), (1, 1), (1, 0)]
    # point near the open gap between last and first vertex
    p = (0.5, -0.1)
    d_open = distance_to_polyline_km(*p, sq, closed=False)
    d_closed = distance_to_polyline_km(*p, sq, closed=True)
    # closed ring includes the (1,0)->(0,0) edge, so it is at least as near
    assert d_closed <= d_open + 1e-9


def test_distance_to_polyline_empty():
    assert distance_to_polyline_km(24, 121, []) is None


def test_point_in_polygon_inside_outside():
    sq = [(0, 0), (0, 2), (2, 2), (2, 0)]
    assert point_in_polygon(1, 1, sq) is True
    assert point_in_polygon(3, 3, sq) is False
    assert point_in_polygon(-0.5, 1, sq) is False


def test_point_in_polygon_degenerate():
    assert point_in_polygon(1, 1, [(0, 0), (1, 1)]) is False
