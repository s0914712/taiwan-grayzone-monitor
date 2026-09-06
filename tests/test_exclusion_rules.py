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


# ── 無效 MMSI（非 9 位數 / MID 未經 ITU 指配）─────────────────
# 實測這批垃圾識別碼長期霸佔高風險榜首：'KKK' (106000000, 36 分)、
# 'HOSM AIS TEST SHIP' (100900000, 33 分)、'00000000000000' (400000000, 30 分)。
# tier-1 有 2,917 艘 MID 無效，其中 2,412 艘早已被浮標/漁網規則擋下，
# 這兩條規則多攔下 505 艘。

def test_unassigned_mid_excluded():
    assert _excluded("106000000", ["KKK"])
    assert _excluded("100900000", ["HOSM AIS TEST SHIP"])
    assert _excluded("400000000", ["00000000000000"])


def test_malformed_mmsi_excluded():
    # 去零的岸台/AtoN 與設備誤設的短碼漁網信標
    assert _excluded("2680005", ["MINHUIYU00268-05"])
    assert _excluded("66750010", ["MINQUANYU06675-10"])
    assert _excluded("", ["NO MMSI"])


def test_assigned_mid_not_excluded():
    """真實船旗一律放行 — 含制裁油輪常見的尼加拉瓜(350)/喀麥隆(613)。"""
    for mmsi in ("412446229", "413875010", "416123456",
                 "350179000", "613271610", "620999970", "677048900"):
        assert not _excluded(mmsi, ["SOME VESSEL"]), mmsi


def test_mid_rule_disabled_when_table_missing(monkeypatch):
    """MID 表載入失敗 → 停用規則，寧可全部照常評分也不要整批誤排除。"""
    import analyze_suspicious as a
    assert a.has_unassigned_mid("106000000", valid_mids=frozenset()) is False
    assert a.has_unassigned_mid("106000000", valid_mids=frozenset({"412"})) is True


def test_valid_mid_table_loads_from_repo(tmp_path, monkeypatch):
    """DATA_DIR 是相對路徑；不在 repo 根目錄執行時要能回推到檔案位置。"""
    import analyze_suspicious as a
    monkeypatch.setattr(a, "MID_FLAGS_FILE", tmp_path / "nope.json")
    monkeypatch.setattr(a, "_valid_mids_cache", None)
    mids = a.load_valid_mids()
    assert len(mids) >= 290 and "412" in mids and "416" in mids
