from analyze_suspicious import check_exclusion_rules


def _excluded(mmsi, names):
    excluded, _ = check_exclusion_rules(mmsi, names)
    return excluded


def test_mmsi_9_prefix_excluded():
    assert _excluded("991234567", ["SOME NAME"])


def test_mmsi_898_prefix_excluded():
    assert _excluded("898123456", ["SOME NAME"])


def test_percent_name_excluded():
    assert _excluded("412345678", ["NET 80%"])


def test_buoy_name_excluded():
    assert _excluded("412345678", ["LIGHT BUOY 3"])


def test_voltage_suffix_excluded():
    assert _excluded("412345678", ["MARKER 12.5V"])


def test_normal_vessel_not_excluded():
    excluded, matched = check_exclusion_rules("412345678", ["MIN SHI YU 07771"])
    assert not excluded
    assert matched == []


def test_gov_vessel_not_excluded():
    assert not _excluded("413456789", ["HAIJING 2304"])
