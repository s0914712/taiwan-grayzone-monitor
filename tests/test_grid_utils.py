"""格網統計工具 — grid_utils.py"""
import grid_utils as gu


def test_grid_cell_matches_match_sar_ais_scheme():
    """取整方式須與 match_sar_ais.build_density_grid 相同：round(x/cell)*cell。"""
    assert gu.grid_cell(24.04, 121.06) == (
        round(24.04 / 0.1) * 0.1, round(121.06 / 0.1) * 0.1)
    lat, lon = gu.grid_cell(24.04, 121.04)
    assert abs(lat - 24.0) < 1e-9
    assert abs(lon - 121.0) < 1e-9


def test_build_stat_grid_counts_distinct_vessels():
    events = [
        {'lat': 24.01, 'lon': 121.01, 'mmsi': 'A', 'hours': 4.0, 'avg_speed_kn': 2.0},
        {'lat': 24.02, 'lon': 121.02, 'mmsi': 'A', 'hours': 3.0, 'avg_speed_kn': 1.0},
        {'lat': 24.03, 'lon': 121.03, 'mmsi': 'B', 'hours': 5.0, 'avg_speed_kn': 3.0},
        {'lat': 25.5, 'lon': 122.5, 'mmsi': 'C', 'hours': 6.0, 'avg_speed_kn': 2.0},
    ]
    grid = gu.build_stat_grid(events)
    assert len(grid) == 2
    top = grid[0]  # 三事件同格：4+3+5 = 12h > 6h
    assert top['events'] == 3
    assert top['vessels'] == 2               # A, B 不重複
    assert top['loiter_hours'] == 12.0
    # 時數加權均速：(2*4 + 1*3 + 3*5) / 12 = 26/12 ≈ 2.2
    assert top['avg_speed_kn'] == 2.2
    assert grid[1]['vessels'] == 1


def test_build_stat_grid_none_speed_and_empty():
    grid = gu.build_stat_grid([
        {'lat': 24.0, 'lon': 121.0, 'mmsi': 'A', 'hours': 4.0,
         'avg_speed_kn': None}])
    assert grid[0]['avg_speed_kn'] is None
    assert gu.build_stat_grid([]) == []


def test_build_stat_grid_skips_missing_coords():
    grid = gu.build_stat_grid([
        {'lat': None, 'lon': 121.0, 'mmsi': 'A', 'hours': 4.0,
         'avg_speed_kn': 1.0}])
    assert grid == []
