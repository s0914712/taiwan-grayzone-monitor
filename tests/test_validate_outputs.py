import json

from validate_outputs import check_file


def _ok(d):
    return isinstance(d, dict) and len(d.get("vessels", [])) > 0


def test_check_file_valid(tmp_path):
    p = tmp_path / "snap.json"
    p.write_text(json.dumps({"vessels": [{"mmsi": "412345678"}]}))
    assert check_file(str(p), _ok) is None


def test_check_file_missing(tmp_path):
    assert check_file(str(tmp_path / "nope.json"), _ok) == "檔案不存在"


def test_check_file_corrupt(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("garbage{{{")
    assert "無法解析" in check_file(str(p), _ok)


def test_check_file_empty_vessels(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"vessels": []}))
    assert check_file(str(p), _ok) is not None


def test_check_file_validator_exception_is_reported(tmp_path):
    p = tmp_path / "weird.json"
    p.write_text(json.dumps([1, 2, 3]))
    # validator 對 list 呼叫 .get → TypeError/AttributeError 應被轉成失敗訊息
    assert "結構檢查失敗" in check_file(str(p), lambda d: d.get("x") or True)


# ── SAR×AIS 管線守衛（2026-07~09 靜默七週的回歸測試）────────────────────
# 那段期間暗偵測只有 322 筆、in_ais_coverage=0、重比對 0，取證清單整份空白，
# darkship cron 每晚空轉 —— 而所有 workflow 全綠，因為沒有任何檢查看這條管線。

from validate_outputs import CHECKS


def _validator(path):
    for p, fn, required in CHECKS:
        if p == path:
            return fn, required
    raise AssertionError(f"{path} 不在 CHECKS 裡")


def test_sar_matches_guard_rejects_the_dead_rematch_state():
    fn, required = _validator('data/sar_ais_matches.json')
    assert required is True          # 必須擋下來，不能只是警告
    assert fn({'summary': {'dark_total': 322, 'in_ais_coverage': 0,
                           'rematched_local': 0, 'residual_dark': 302}}) is False
    assert fn({'summary': {'dark_total': 0, 'in_ais_coverage': 0}}) is False
    assert fn({}) is False
    assert fn({'summary': {'dark_total': 14520, 'in_ais_coverage': 12679}}) is True


def test_worklist_guard_warns_when_the_forensics_queue_is_starved():
    fn, required = _validator('data/sar_chip_worklist.json')
    # Sentinel-1 東部覆蓋本來就稀疏，偶爾真的 0 筆 → 只警告不擋 commit
    assert required is False
    assert fn({'summary': {'residual_total': 302, 'in_zone': 0, 'listed': 0}}) is False
    assert fn({'summary': {'residual_total': 12268, 'in_zone': 469}}) is True
