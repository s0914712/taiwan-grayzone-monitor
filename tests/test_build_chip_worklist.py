"""Tests for build_chip_worklist.py — zone filter, footprint parsing,
point-in-footprint coverage, and worklist assembly. No network."""

import build_chip_worklist as bw


# ── 關注海域 ────────────────────────────────────────────────────────────────

def test_zone_of():
    assert bw.zone_of(23.2, 122.2) == 'east'
    assert bw.zone_of(22.0, 119.5) == 'southwest'
    assert bw.zone_of(25.0, 119.0) is None      # 台灣海峽北部 — 不在關注區
    assert bw.zone_of(30.0, 125.0) is None


# ── 足跡解析 ────────────────────────────────────────────────────────────────

def _poly(lon0, lat0, lon1, lat1):
    return {'type': 'Polygon', 'coordinates': [[
        [lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0],
    ]]}


def test_footprint_polygon_and_multipolygon():
    rings = bw.footprint_polygons(_poly(121.5, 22.0, 124.0, 24.0))
    assert len(rings) == 1 and len(rings[0]) == 5
    assert rings[0][0] == (22.0, 121.5)          # (lat, lon) 順序

    multi = {'type': 'MultiPolygon',
             'coordinates': [_poly(121.5, 22.0, 124.0, 24.0)['coordinates'],
                             _poly(117.0, 20.0, 120.0, 23.0)['coordinates']]}
    assert len(bw.footprint_polygons(multi)) == 2

    assert bw.footprint_polygons(None) == []
    assert bw.footprint_polygons({'type': 'Point', 'coordinates': [1, 2]}) == []
    assert bw.footprint_polygons({'type': 'Polygon', 'coordinates': []}) == []


def _product(name, start, footprint):
    return {'Name': name, 'ContentDate': {'Start': start},
            'GeoFootprint': footprint}


def test_prepare_products_and_coverage():
    prods = bw.prepare_products([
        _product('S1A_IW_GRDH_A', '2026-06-26T21:54:02.000Z',
                 _poly(121.5, 22.0, 124.0, 24.0)),
        _product('BAD_DATE', 'nope', _poly(0, 0, 1, 1)),
        _product('NO_FOOT', '2026-06-26T21:54:02.000Z', None),
    ])
    assert len(prods) == 1
    p = prods[0]
    assert p['date'] == '2026-06-26' and p['time'] == '21:54'
    assert bw.product_covers(p, 23.2, 122.2)
    assert not bw.product_covers(p, 23.2, 125.0)


# ── 清單組裝 ────────────────────────────────────────────────────────────────

def _residual(lat, lon, date, zone='eez', cov=True):
    return {'lat': lat, 'lon': lon, 'date': date, 'zone': zone,
            'in_ais_coverage': cov}


def test_build_worklist_covered_and_uncovered():
    products = bw.prepare_products([
        _product('S1A_IW_GRDH_E', '2026-06-26T21:54:02.000Z',
                 _poly(121.5, 22.0, 124.0, 24.0)),      # 涵蓋東部
    ])
    residual = [
        _residual(23.2, 122.2, '2026-06-26'),   # 東部、有涵蓋
        _residual(22.8, 122.1, '2026-06-25'),   # 東部、該日無產品
        _residual(21.5, 118.5, '2026-06-26'),   # 西南、產品足跡外
        _residual(26.5, 120.0, '2026-06-26'),   # 關注區外 → 不進清單
    ]
    targets, zone_counts = bw.build_worklist(residual, products)
    assert len(targets) == 3
    assert zone_counts == {'east': 2, 'southwest': 1}

    hit = next(t for t in targets if t['covered'])
    assert hit['product'] == 'S1A_IW_GRDH_E'
    assert hit['time'] == '21:54:02'
    assert hit['command'] == 'python fetch_sar_chip.py 23.2 122.2 2026-06-26 --time 21:54'

    misses = [t for t in targets if not t['covered']]
    assert len(misses) == 2
    assert all(t['command'] is None for t in misses)

    # 排序：日期新→舊；同日期 covered 在前
    assert targets[0]['date'] == '2026-06-26' and targets[0]['covered']


def test_build_worklist_picks_earliest_pass_and_keeps_alternates():
    products = bw.prepare_products([
        _product('DESC', '2026-06-26T21:54:02.000Z', _poly(121.5, 22.0, 124.0, 24.0)),
        _product('ASC', '2026-06-26T09:51:23.000Z', _poly(121.5, 22.0, 124.0, 24.0)),
    ])
    targets, _ = bw.build_worklist([_residual(23.0, 122.0, '2026-06-26')], products)
    t = targets[0]
    assert t['product'] == 'ASC'                 # 時間較早者為主
    assert t['alt_times'] == ['21:54:02']


def test_build_worklist_caps_targets():
    products = []
    residual = [_residual(23.0, 122.0, f'2026-06-{d:02d}') for d in range(1, 29)]
    targets, _ = bw.build_worklist(residual, products, max_targets=10)
    assert len(targets) == 10
    assert targets[0]['date'] == '2026-06-28'    # 最新優先
