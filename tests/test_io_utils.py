import json
import os

from io_utils import atomic_write_json, load_json, make_retry_session


def test_atomic_write_roundtrip(tmp_path):
    path = tmp_path / "out.json"
    data = {"名稱": "海警2304", "vessels": [1, 2, 3], "nested": {"a": None}}
    atomic_write_json(path, data)
    assert json.loads(path.read_text(encoding="utf-8")) == data
    # 同目錄不留 temp 檔
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]


def test_atomic_write_compact(tmp_path):
    path = tmp_path / "compact.json"
    atomic_write_json(path, {"a": [1, 2]}, compact=True)
    assert path.read_text(encoding="utf-8") == '{"a":[1,2]}'


def test_atomic_write_overwrites_existing(tmp_path):
    path = tmp_path / "out.json"
    atomic_write_json(path, {"v": 1})
    atomic_write_json(path, {"v": 2})
    assert json.loads(path.read_text()) == {"v": 2}


def test_atomic_write_failure_keeps_original_and_cleans_tmp(tmp_path):
    path = tmp_path / "out.json"
    atomic_write_json(path, {"v": 1})
    try:
        atomic_write_json(path, {"bad": object()})  # 不可序列化 → 寫入失敗
    except TypeError:
        pass
    # 原檔完好、temp 檔已清掉
    assert json.loads(path.read_text()) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]


def test_atomic_write_creates_parent_dir(tmp_path):
    path = tmp_path / "sub" / "dir" / "out.json"
    atomic_write_json(path, [])
    assert json.loads(path.read_text()) == []


def test_load_json_missing_returns_default(tmp_path):
    assert load_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}


def test_load_json_corrupt_returns_default(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("not json{{{")
    assert load_json(path, [], label="壞檔") == []
    assert "壞檔" in capsys.readouterr().out


def test_load_json_expect_type_mismatch(tmp_path, capsys):
    path = tmp_path / "list.json"
    path.write_text("[1,2,3]")
    assert load_json(path, {}, expect_type=dict) == {}
    assert "型別不符" in capsys.readouterr().out
    assert load_json(path, [], expect_type=list) == [1, 2, 3]


def test_make_retry_session_mounts_adapters():
    s = make_retry_session(total=5)
    for prefix in ("http://", "https://"):
        adapter = s.get_adapter(prefix + "example.com")
        assert adapter.max_retries.total == 5
        assert 429 in adapter.max_retries.status_forcelist
