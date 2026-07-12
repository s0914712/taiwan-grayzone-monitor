"""Tests for fetch_sar_chip.py pure functions — OData filter/product choice,
measurement selection, GCP lat/lon→pixel conversion, and target extraction.
No network / no boto3 / no rasterio needed."""

import math

import pytest

import fetch_sar_chip as fc


# ── OData 查詢與產品挑選 ─────────────────────────────────────────────────────

def test_build_point_filter():
    f = fc.build_point_filter(24.46, 118.59, '2026-06-26')
    assert "POINT(118.59000 24.46000)" in f
    assert "contains(Name,'_IW_GRDH_')" in f
    assert "ContentDate/Start ge 2026-06-26T00:00:00.000Z" in f
    assert "ContentDate/Start le 2026-06-26T23:59:59.999Z" in f


def _product(name, start, s3path='/eodata/Sentinel-1/SAR/IW_GRDH_1S/x'):
    return {'Name': name, 'ContentDate': {'Start': start}, 'S3Path': s3path}


def test_choose_product_prefers_requested_time():
    prods = [
        _product('ASC', '2026-06-26T09:51:23.000Z'),
        _product('DESC', '2026-06-26T21:54:02.000Z'),
    ]
    assert fc.choose_product(prods, '21:55')['Name'] == 'DESC'
    assert fc.choose_product(prods, '09:50')['Name'] == 'ASC'
    assert fc.choose_product(prods)['Name'] == 'ASC'   # 無偏好 → 第一個有效
    assert fc.choose_product([]) is None
    # 缺 S3Path / 壞時間戳的產品略過
    assert fc.choose_product([{'Name': 'X', 'ContentDate': {'Start': 'bad'}}]) is None


def test_pick_measurement_key_prefers_vv():
    keys = [
        'S1A.SAFE/measurement/s1a-iw-grd-vh-20260626.tiff',
        'S1A.SAFE/measurement/s1a-iw-grd-vv-20260626.tiff',
        'S1A.SAFE/manifest.safe',
    ]
    assert fc.pick_measurement_key(keys).endswith('vv-20260626.tiff')
    assert fc.pick_measurement_key(['a/manifest.safe']) is None
    assert fc.pick_measurement_key([]) is None
    # 無 VV → 退第一個 tiff
    assert fc.pick_measurement_key(
        ['m/s1a-iw-grd-vh-1.tiff']).endswith('vh-1.tiff')


# ── GCP 經緯度 → 像素 ────────────────────────────────────────────────────────

def _synthetic_gcps():
    """10×10 GCP 網格：col = (lon-118)*1000, row = (24.5-lat)*1200 加一點
    非線性彎曲（模擬 S1 GRD 幾何）。"""
    gcps = []
    for i in range(10):
        for j in range(10):
            lat = 24.5 - i * 0.02
            lon = 118.0 + j * 0.025
            row = i * 24 + 0.5 * j          # 輕微斜切
            col = j * 25 + 0.002 * (i * j)  # 輕微非線性
            gcps.append((row, col, lon, lat))
    return gcps


def test_latlon_to_pixel_recovers_gcp_positions():
    gcps = _synthetic_gcps()
    # 在 GCP 本身上誤差應該非常小（殘差修正把它拉回去）
    for row, col, lon, lat in gcps[::17]:
        pr, pc = fc.latlon_to_pixel(gcps, lat, lon)
        assert abs(pr - row) < 1.5
        assert abs(pc - col) < 1.5


def test_latlon_to_pixel_interpolates_between_gcps():
    gcps = _synthetic_gcps()
    # 網格中間點：仿射 + 殘差修正應在數 px 內
    pr, pc = fc.latlon_to_pixel(gcps, 24.5 - 0.03, 118.0 + 0.0375)
    assert abs(pr - 36) < 4      # i=1.5 → row≈36
    assert abs(pc - 37.5) < 4    # j=1.5 → col≈37.5


def test_latlon_to_pixel_requires_three_gcps():
    with pytest.raises(ValueError):
        fc.latlon_to_pixel([(0, 0, 118.0, 24.0)], 24.0, 118.0)


# ── 亮目標擷取與長度粗估 ─────────────────────────────────────────────────────

def _sea_chip(h=120, w=120, sea=100):
    return [[sea for _ in range(w)] for _ in range(h)]


def test_no_target_on_uniform_sea():
    r = fc.estimate_target_extent(_sea_chip())
    assert r['found'] is False


def test_ship_target_length_estimated():
    chip = _sea_chip()
    # 中心附近放一艘 12px 長、2px 寬的斜船（10m/px → ~120m）
    for k in range(12):
        chip[60 + k][60 + k] = 5000
        chip[60 + k][61 + k] = 5000
    r = fc.estimate_target_extent(chip)
    assert r['found'] is True
    # 對角 12px 斜船：包絡對角 ≈ sqrt(13²+13²)·10m ≈ 184m — 同數量級即可
    assert 100 <= r['length_m'] <= 260
    assert r['peak_ratio'] > 10
    assert not r['saturated']


def test_target_far_from_center_is_ignored():
    chip = _sea_chip()
    chip[5][5] = 9000   # 亮，但在中心搜尋窗外
    r = fc.estimate_target_extent(chip)
    assert r['found'] is False


def test_tiny_chip_rejected():
    assert fc.estimate_target_extent([[1, 2], [3, 4]])['found'] is False
