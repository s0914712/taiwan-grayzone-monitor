"""Tests for the exact-MMSI known-gov table + gov-candidate heuristic
(src/fetch_ais_data.py) — 海警1401 案例：名稱關鍵字之外的第二辨識路徑。"""
import fetch_ais_data as f


# ── classify_gov_vessel：名稱優先，MMSI 對照表補位 ──────────────────────────

def test_name_match_still_first():
    assert f.classify_gov_vessel('CHINACOASTGUARD1401', '413875010') == 'coastguard'
    assert f.classify_gov_vessel('HAIXUN1620', None) == 'msa'


def test_known_mmsi_catches_renamed_ccg():
    # 海警1401 改播數字/無意義船名時，MMSI 精確對照表仍須辨識
    assert f.classify_gov_vessel('1401', '413875010') == 'coastguard'
    assert f.classify_gov_vessel('', '413875010') == 'coastguard'
    assert f.classify_gov_vessel(None, 413875010) == 'coastguard'


def test_shared_block_not_prefix_matched():
    # 413875 段與民船共用 — 對照表是精確比對，段內未收錄的 MMSI 不得命中
    assert f.classify_gov_vessel('HUAHANG10DP', '413875213') is None


def test_civilian_name_and_mmsi_none():
    assert f.classify_gov_vessel('MIN SHI YU 07771', '412345678') is None


# ── is_gov_candidate：純數字船名＋中國 MID ──────────────────────────────────

def test_pure_digit_name_cn_mid_is_candidate():
    assert f.is_gov_candidate('1401', '413000999')
    assert f.is_gov_candidate('2 3 0 5', '412111222')  # 去空白後純數字


def test_digit_suffix_fishing_name_not_candidate():
    # 「船名含四位數字」不能當判定 — 閩夏漁01401 是漁船
    assert not f.is_gov_candidate('MINXIAYU01401', '412999888')


def test_pure_digit_name_non_cn_mid_not_candidate():
    assert not f.is_gov_candidate('1401', '416000111')  # Taiwan MID


def test_candidate_edge_cases():
    assert not f.is_gov_candidate('', '413000999')
    assert not f.is_gov_candidate('1401', None)
    assert not f.is_gov_candidate('12', '413000999')       # 太短
    assert not f.is_gov_candidate('123456', '413000999')   # 太長（漁船編號）
