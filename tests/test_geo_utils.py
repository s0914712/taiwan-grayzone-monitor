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
