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
