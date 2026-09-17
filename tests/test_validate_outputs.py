import json
from datetime import datetime, timedelta, timezone

from validate_outputs import check_ais_snapshot, check_file


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


# ── AIS 快照新鮮度（2026-09 代理黑名單事件的盲點） ─────────────────────────

def _snapshot(hours_ago):
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"updated_at": ts.isoformat(), "vessels": [{"mmsi": "412345678"}]}


def test_ais_snapshot_fresh_passes():
    assert check_ais_snapshot(_snapshot(1)) is True


def test_ais_snapshot_stale_reports_age():
    reason = check_ais_snapshot(_snapshot(240))  # 十天沒更新 = 事件當時的狀態
    assert isinstance(reason, str) and "未更新" in reason


def test_ais_snapshot_accepts_z_suffix():
    ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    snap = {"updated_at": ts.replace("+00:00", "Z"), "vessels": [{"mmsi": "1"}]}
    assert check_ais_snapshot(snap) is True


def test_ais_snapshot_unparseable_timestamp():
    assert check_ais_snapshot({"updated_at": "昨天", "vessels": [{"mmsi": "1"}]}) \
        == "updated_at 缺漏或無法解析"


def test_ais_snapshot_empty_still_fails():
    assert check_ais_snapshot({"updated_at": datetime.now(timezone.utc).isoformat(),
                               "vessels": []}) is False


def test_check_file_surfaces_validator_reason(tmp_path):
    """驗證函式回傳字串時，該字串要原樣當成失敗原因。"""
    p = tmp_path / "stale.json"
    p.write_text(json.dumps(_snapshot(240)))
    reason = check_file(str(p), check_ais_snapshot)
    assert "未更新" in reason
