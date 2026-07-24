"""Tests for src/match_sar_ais.py — SAR × local-AIS re-matching.

All synthetic: tracks/detections are built inline and passed to the pure
functions (estimate_position / run_matching / ...), so nothing depends on the
committed data files. The CI test env has no scipy, which also exercises the
greedy-assignment fallback path.
"""
import math

import pytest

import match_sar_ais as m
from geo_utils import haversine_km


HOUR = 3600.0
T0 = 1_600_000_000.0  # arbitrary epoch anchor


def pt(t, lat, lon, speed=0.0, heading=0.0):
    return (t, lat, lon, speed, heading)


# ── estimate_position：內插與航位推算 ────────────────────────────────────────

def test_interpolation_midpoint():
    points = [pt(T0, 24.0, 120.0, 10, 90), pt(T0 + 2 * HOUR, 24.0, 120.4, 10, 90)]
    e = m.estimate_position(points, T0 + HOUR)
    assert e['method'] == 'interpolated'
    assert e['lat'] == pytest.approx(24.0)
    assert e['lon'] == pytest.approx(120.2)
    assert e['dt_h'] == pytest.approx(1.0)


def test_dead_reckoning_projects_along_heading():
    # 10 kn due east for 1 h → 18.52 km east of the last fix
    points = [pt(T0, 24.0, 120.0, 10, 90)]
    e = m.estimate_position(points, T0 + HOUR)
    assert e['method'] == 'dead_reckoning'
    d = haversine_km(24.0, 120.0, e['lat'], e['lon'])
    assert d == pytest.approx(18.52, abs=0.1)
    assert e['lon'] > 120.0
    assert e['lat'] == pytest.approx(24.0, abs=0.01)


def test_dead_reckoning_backward_in_time():
    # SAR time BEFORE the only fix → project along reciprocal heading
    points = [pt(T0 + HOUR, 24.0, 120.0, 10, 90)]
    e = m.estimate_position(points, T0)
    assert e['method'] == 'dead_reckoning'
    assert e['lon'] < 120.0  # heading east ⇒ 1 h earlier it was to the west


def test_stationary_vessel_holds_position():
    points = [pt(T0, 24.0, 120.0, 0.0, 90)]
    e = m.estimate_position(points, T0 + HOUR)
    assert e['method'] == 'hold'
    assert e['lat'] == 24.0 and e['lon'] == 120.0
    assert e['unc_extra_km'] == m.STATIONARY_UNC_KM


def test_moving_vessel_without_heading_gets_wide_uncertainty():
    points = [pt(T0, 24.0, 120.0, 10, None)]
    e = m.estimate_position(points, T0 + HOUR)
    assert e['method'] == 'hold'
    assert e['unc_extra_km'] == pytest.approx(10 * m.KN_TO_KMH * 1.0)


def test_too_old_fix_returns_none():
    points = [pt(T0, 24.0, 120.0, 10, 90)]
    assert m.estimate_position(points, T0 + (m.MAX_DT_HOURS + 0.5) * HOUR) is None
    assert m.estimate_position([], T0) is None


# ── 動態 gate ────────────────────────────────────────────────────────────────

def test_gate_is_dynamic_and_clamped():
    assert m.gate_km(0.0) == max(m.MIN_GATE_KM, m.BASE_ERROR_KM)
    mid = m.gate_km(3.0)
    assert mid == pytest.approx(m.BASE_ERROR_KM + 3.0)
    assert m.gate_km(1000.0) == m.MAX_GATE_KM


# ── 一對一指派（貪婪 fallback；有 scipy 時同組亦驗證匈牙利）─────────────────

def _crossing_candidates():
    # Both detections are closest to vessel A: a one-to-many matcher would
    # give both to A; ours must split them A/B one-to-one.
    return {
        0: [(0.2, 'A', 1.0, 5.0), (0.6, 'B', 3.0, 5.0)],
        1: [(0.3, 'A', 1.5, 5.0), (0.5, 'B', 2.5, 5.0)],
    }


def test_greedy_assignment_is_one_to_one():
    result = m._solve_greedy(_crossing_candidates())
    assert result[0][1] == 'A'
    assert result[1][1] == 'B'  # A already taken by the cheaper pair


def test_hungarian_matches_greedy_on_simple_case():
    pytest.importorskip('scipy')
    result = m._solve_hungarian(_crossing_candidates())
    assert result[0][1] == 'A'
    assert result[1][1] == 'B'


def test_hungarian_finds_globally_optimal_swap():
    pytest.importorskip('scipy')
    # Greedy takes (det0,A)=0.10 then forces (det1,B)=0.90 (total 1.00);
    # optimal is (det0,B)=0.15 + (det1,A)=0.20 (total 0.35).
    cands = {
        0: [(0.10, 'A', 1.0, 10.0), (0.15, 'B', 1.5, 10.0)],
        1: [(0.20, 'A', 2.0, 10.0), (0.90, 'B', 9.0, 10.0)],
    }
    result = m._solve_hungarian(cands)
    assert result[0][1] == 'B'
    assert result[1][1] == 'A'


# ── 固定設施重現性啟發式 ─────────────────────────────────────────────────────

def test_recurring_cell_flagged_as_infrastructure():
    recs = [{'lat': 24.001, 'lon': 120.001, 'date': f'2026-07-{d:02d}'}
            for d in range(1, m.INFRA_MIN_DATES + 1)]
    recs.append({'lat': 25.5, 'lon': 121.5, 'date': '2026-07-01'})
    cells = m.detect_infrastructure_cells(recs)
    assert m.infra_cell(24.001, 120.001) in cells
    assert m.infra_cell(25.5, 121.5) not in cells


def test_static_mask_filters_detection():
    is_infra, _ = m.build_infra_filter([], [(24.0, 120.0, 1.0)])
    assert is_infra({'lat': 24.003, 'lon': 120.0, 'date': '2026-07-01'})
    assert not is_infra({'lat': 24.1, 'lon': 120.0, 'date': '2026-07-01'})


# ── 跨執行偵測歷史（cron 累積 → 重現性判準）───────────────────────────────────

def test_update_detection_history_accumulates_and_trims():
    now = m.parse_ts('2026-07-23T00:00:00+00:00')
    # 第一次執行：同一粗網格、兩個不同過境日（座標略有抖動仍落回同格）
    h = m.update_detection_history({}, [
        {'lat': 24.30, 'lon': 120.33, 'date': '2026-07-01'},
        {'lat': 24.305, 'lon': 120.332, 'date': '2026-07-08'},
    ], now)
    key = m._cell_key(*m.infra_cell(24.30, 120.33))
    assert h['cells'][key] == ['2026-07-01', '2026-07-08']
    assert h['cell_deg'] == m.INFRA_CELL_DEG

    # 第二次執行：新增一個過境日 + 一個超過保留期的舊日（應被裁掉）
    h2 = m.update_detection_history(h, [
        {'lat': 24.30, 'lon': 120.33, 'date': '2026-07-15'},
        {'lat': 24.30, 'lon': 120.33, 'date': '2026-01-01'},   # >90 天 → 裁切
        {'lat': 24.30, 'lon': 120.33, 'date': '2026-07-01'},   # 重覆日 → 去重
    ], now)
    assert '2026-07-15' in h2['cells'][key]
    assert '2026-01-01' not in h2['cells'][key]
    assert h2['cells'][key].count('2026-07-01') == 1


def test_recurring_cells_from_history_threshold():
    hist = {'cells': {
        '810,4011': ['2026-07-01', '2026-07-08', '2026-07-15'],  # ≥3 日 → 設施
        '999,999': ['2026-07-01', '2026-07-08'],                 # 2 日 → 非設施
    }}
    rec = m.recurring_cells_from_history(hist)
    assert (810, 4011) in rec
    assert (999, 999) not in rec


def test_infra_filter_uses_history_recurrence():
    # 當下窗此格只有 1 筆（單窗判不出重現），但歷史顯示跨 3 個過境日重現
    ci, cj = m.infra_cell(24.30, 120.33)
    recurring = {(ci, cj): ['2026-07-01', '2026-07-08', '2026-07-15']}
    is_infra, cells = m.build_infra_filter(
        [{'lat': 24.30, 'lon': 120.33, 'date': '2026-07-22'}], [],
        recurring=recurring)
    assert is_infra({'lat': 24.30, 'lon': 120.33, 'date': '2026-07-22'})
    assert not is_infra({'lat': 26.0, 'lon': 122.0, 'date': '2026-07-22'})


# ── 非船舶 AIS 發射器排除 ────────────────────────────────────────────────────

def test_nonvessel_ais_excluded():
    assert m.is_nonvessel_ais('994123456', 'SOME ATON')       # AtoN MMSI
    assert m.is_nonvessel_ais('898001122', 'NET MARK')        # 漁網信標段
    assert m.is_nonvessel_ais('150895811', 'YU 08958-11-98%')  # % 名稱
    assert m.is_nonvessel_ais('412000001', 'CHANNEL BUOY 3')
    assert m.is_nonvessel_ais('412000001', 'BEACON 12.5V')
    assert not m.is_nonvessel_ais('412447675', 'MINLONGYU62225')
    assert not m.is_nonvessel_ais('416000123', 'EVER GIVEN')


# ── 船長交叉驗證 ─────────────────────────────────────────────────────────────

def test_length_mismatch_thresholds():
    assert m.check_length_mismatch(200.0, 80.0) is True    # 60% & 120 m off
    assert m.check_length_mismatch(100.0, 90.0) is False   # 10% off
    assert m.check_length_mismatch(40.0, 20.0) is False    # 50% but only 20 m
    assert m.check_length_mismatch(None, 90.0) is None
    assert m.check_length_mismatch(100.0, None) is None


def test_get_ais_length_from_dims():
    assert m.get_ais_length_m({'dim_a': 100, 'dim_b': 20}) == 120.0
    assert m.get_ais_length_m({'length_m': 85.0}) == 85.0
    assert m.get_ais_length_m({}) is None
    assert m.get_ais_length_m(None) is None


# ── run_matching 端到端（合成資料）───────────────────────────────────────────

def _track(name, type_name, points):
    return {'name': name, 'type_name': type_name, 'points': points}


def test_run_matching_end_to_end():
    # Ascending pass on 2026-07-10 ≈ 09:50 UTC
    t_pass = m.parse_ts('2026-07-10T09:50:00+00:00')
    # Vessel with AIS fixes 50 min either side of the pass, sailing east
    # through (24.0, 120.0) exactly at pass time.
    v_lon0 = 120.0 - (10 * m.KN_TO_KMH * (50 / 60)) / (111.32 * math.cos(math.radians(24)))
    v_lon1 = 120.0 + (10 * m.KN_TO_KMH * (50 / 60)) / (111.32 * math.cos(math.radians(24)))
    tracks = {
        '412000001': _track('REMATCH ME', 'cargo', [
            pt(t_pass - 50 * 60, 24.0, v_lon0, 10, 90),
            pt(t_pass + 50 * 60, 24.0, v_lon1, 10, 90),
        ]),
    }
    dark = [
        # ① matchable by local AIS at the pass position
        {'lat': 24.0, 'lon': 120.0, 'date': '2026-07-10', 'detections': 1},
        # ② genuinely dark: nothing anywhere near
        {'lat': 22.0, 'lon': 118.0, 'date': '2026-07-10', 'detections': 1},
        # ③ before AIS coverage (a week earlier than any fix)
        {'lat': 23.0, 'lon': 119.0, 'date': '2026-07-03', 'detections': 1},
    ]
    # ④ recurring infrastructure cell
    dark += [{'lat': 23.5001, 'lon': 119.5001, 'date': f'2026-07-{d:02d}',
              'detections': 1} for d in range(4, 4 + m.INFRA_MIN_DATES)]

    result = m.run_matching(dark, tracks)
    s = result['summary']

    assert s['dark_total'] == 3 + m.INFRA_MIN_DATES
    assert s['infrastructure_filtered'] == m.INFRA_MIN_DATES
    assert s['rematched_local'] == 1
    assert s['residual_dark'] == 2

    # 逐筆固定設施清單：長度 == 過濾數，且每筆帶來源 reason（前端卡片判決用）
    infra = result['infrastructure']
    assert len(infra) == m.INFRA_MIN_DATES
    assert all(e['reason'] == 'recurrence' for e in infra)
    assert all(e['lat'] == 23.5001 for e in infra)

    rm = result['rematched'][0]
    assert rm['mmsi'] == '412000001'
    assert rm['method'] == 'interpolated'
    assert rm['distance_km'] <= rm['gate_km']
    assert rm['pass'] == 'ascending'

    residual = {(r['lat'], r['lon']): r for r in result['residual_dark']}
    assert residual[(22.0, 118.0)]['in_ais_coverage'] is True
    assert residual[(23.0, 119.0)]['in_ais_coverage'] is False

    # 輸出層：密度網格與時間序列
    assert any(c['count'] >= 1 for c in result['density_grid'])
    zs = result['zone_series']
    assert '2026-07-10' in zs['dates']
    i = zs['dates'].index('2026-07-10')
    assert zs['raw_total'][i] == 2       # ①② (infra cells are other dates... )
    assert zs['screened_total'][i] == 1  # only ② survives screening


def test_run_matching_one_vessel_not_matched_to_two_detections_same_pass():
    t_pass = m.parse_ts('2026-07-10T09:50:00+00:00')
    tracks = {
        '412000002': _track('LONE', 'cargo', [
            pt(t_pass - 10 * 60, 24.0, 120.0, 0.0, 0),
            pt(t_pass + 10 * 60, 24.0, 120.0, 0.0, 0),
        ]),
    }
    dark = [
        {'lat': 24.0, 'lon': 120.0, 'date': '2026-07-10', 'detections': 1},
        {'lat': 24.005, 'lon': 120.005, 'date': '2026-07-10', 'detections': 1},
    ]
    result = m.run_matching(dark, tracks)
    # 一船同過境只能吸收一筆偵測；另一筆留在殘餘
    assert result['summary']['rematched_local'] == 1
    assert result['summary']['residual_dark'] == 1


def test_pass_candidates_prefers_exact_timestamp():
    rec = {'date': '2026-07-10', 'timestamp': '2026-07-10T21:53:12Z'}
    cands = m.pass_candidates(rec)
    assert len(cands) == 1 and cands[0][0] == 'exact'
    rec2 = {'date': '2026-07-10'}
    labels = [c[0] for c in m.pass_candidates(rec2)]
    assert labels == ['ascending', 'descending']
