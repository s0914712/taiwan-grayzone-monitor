"""Tests for src/geofence.py maritime-zone + cable-band classification.

Uses synthetic baselines/cable segments passed explicitly, so the tests do not
depend on the committed data files. NM thresholds: 1° latitude ≈ 111.19 km, and
12 nm = 22.224 km ≈ 0.20°, 24 nm ≈ 0.40°, 200 nm ≈ 3.33° south of the baseline.
"""
import pytest

import geofence


# Unit square baseline (lat 0..1, lon 0..1) as a single polygon.
SQUARE = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
BASELINES = [SQUARE]


def zone_at(lat, lon):
    return geofence.classify_maritime_zone(lat, lon, baselines=BASELINES)["zone"]


def test_inside_baseline_is_internal_waters():
    r = geofence.classify_maritime_zone(0.5, 0.5, baselines=BASELINES)
    assert r["zone"] == "internal_waters"
    assert r["inside_baseline"] is True
    assert r["distance_to_baseline_km"] == 0.0


def test_territorial_sea_within_12nm():
    # ~0.10° south of the baseline edge ≈ 11 km ≈ 6 nm
    assert zone_at(-0.10, 0.5) == "territorial_sea"


def test_contiguous_zone_between_12_and_24nm():
    # ~0.30° south ≈ 33 km ≈ 18 nm
    assert zone_at(-0.30, 0.5) == "contiguous_zone"


def test_eez_between_24nm_and_200nm():
    # ~1.0° south ≈ 111 km ≈ 60 nm
    assert zone_at(-1.0, 0.5) == "eez"


def test_high_seas_beyond_200nm():
    # ~4° south ≈ 445 km ≈ 240 nm
    assert zone_at(-4.0, 0.5) == "high_seas"


def test_unknown_when_no_baseline():
    r = geofence.classify_maritime_zone(0.5, 0.5, baselines=[])
    assert r["zone"] == "unknown"
    assert r["distance_to_baseline_nm"] is None


def test_distance_increases_with_offset():
    near = geofence.classify_maritime_zone(-0.1, 0.5, baselines=BASELINES)
    far = geofence.classify_maritime_zone(-0.5, 0.5, baselines=BASELINES)
    assert far["distance_to_baseline_nm"] > near["distance_to_baseline_nm"]


# ── cable bands ─────────────────────────────────────────────────────────────
# One cable segment running east-west along lat 24.0 from lon 121.0 to 122.0.
CABLE = [{"points": [(24.0, 121.0), (24.0, 122.0)],
          "bbox": (24.0, 121.0, 24.0, 122.0)}]


def band_at(lat, lon):
    return geofence.nearest_cable(lat, lon, segments=CABLE)["cable_band"]


def test_cable_band_within_1km():
    # ~0.005° north of the cable ≈ 0.56 km
    assert band_at(24.005, 121.5) == "within_1km"


def test_cable_band_within_5km():
    # ~0.03° ≈ 3.3 km
    assert band_at(24.03, 121.5) == "within_5km"


def test_cable_band_within_10km():
    # ~0.07° ≈ 7.8 km
    assert band_at(24.07, 121.5) == "within_10km"


def test_cable_band_beyond_10km():
    # ~0.2° ≈ 22 km
    assert band_at(24.2, 121.5) == "beyond_10km"


def test_cable_unknown_when_no_segments():
    r = geofence.nearest_cable(24.0, 121.5, segments=[])
    assert r["cable_band"] == "unknown"
    assert r["nearest_cable_km"] is None


def test_annotate_combines_zone_and_cable(monkeypatch):
    monkeypatch.setattr(geofence, "load_baselines", lambda: BASELINES)
    monkeypatch.setattr(geofence, "load_cable_segments", lambda: CABLE)
    out = geofence.annotate(24.005, 121.5)
    assert "zone" in out and "cable_band" in out
    assert out["cable_band"] == "within_1km"


# ── 台灣港區/錨泊區 ─────────────────────────────────────────
# 2026-W35 第 3 大徘徊熱區（23.0/120.2）是三艘外籍貨輪停在安平商港區
# 300-447 小時，距 PORTS 的「安平漁港」2.5-3.5km（2km 圈外），
# 位置在自然地球海岸線陸側。TPKM3 海纜登陸點就在安平，因此每週被記成
# 「海纜旁長時間低速徘徊」。

def test_anping_basin_berthed_vessels_are_in_port():
    for lat, lon in [(22.9788, 120.1745),    # HAI BAO
                     (22.9784, 120.1736),    # FA ZHAN
                     (22.9677, 120.1714)]:   # OCEAN ANGELA
        assert geofence.is_in_port(lat, lon) == "安平商港區 Anping-basin"


def test_anping_fishing_port_still_matches_ports_table():
    """漁港本身仍由 PORTS 命中 — 錨泊區表不得蓋掉原本的港名。"""
    assert geofence.is_in_port(22.9972, 120.1600) == "安平漁港 Anping"


def test_anchorage_radius_does_not_swallow_open_water():
    # 安平外海 ~8km、以及南邊二仁溪口外都應維持「非港內」
    assert geofence.is_in_port(22.9750, 120.0950) is None
    assert geofence.is_in_port(22.9200, 120.1900) is None
    # 金門料羅灣外錨地（本週最大熱區）刻意不納入 — 那是真訊號不是靠泊
    assert geofence.is_in_port(24.4000, 118.4000) is None


def test_anchorages_excluded_from_moored_taiwan_port_rule():
    """TW_ANCHORAGES 不進 PORTS：停在港區的外籍船仍照常評分，
    只是港內點不計海纜鄰近/徘徊分（見 analyze_suspicious 的 moored_taiwan_port）。"""
    for name, (lat, lon, radius) in geofence.TW_ANCHORAGES.items():
        assert name not in geofence.PORTS
        assert radius > geofence.PORT_EXCLUSION_KM
