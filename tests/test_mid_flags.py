"""data/mid_flags.json ↔ docs/js/map-data.js MID_FLAG_TABLE 同步回歸測試。

JSON 是 Python 端（aggregate_highrisk.py 船籍欄位）的正典來源；JS 表供前端。
兩份必須逐筆一致 — regex 解析 JS（表的格式完全規則），避免 runtime 耦合。
"""
import json
import re
from pathlib import Path

import pytest

import aggregate_highrisk as agg
from analyze_suspicious import TOP_10_FLAG_MIDS

ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / 'data' / 'mid_flags.json'
JS_PATH = ROOT / 'docs' / 'js' / 'map-data.js'


@pytest.fixture(scope='module')
def json_table():
    return json.loads(JSON_PATH.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def js_table():
    src = JS_PATH.read_text(encoding='utf-8')
    m = re.search(r'const MID_FLAG_TABLE = \{(.*?)\n    \};', src, re.S)
    assert m, 'map-data.js 找不到 MID_FLAG_TABLE 區塊'
    entries = re.findall(
        r"'(\d{3})':\s*\{en:'((?:[^'\\]|\\.)*)',zh:'([^']*)'\}", m.group(1))
    assert entries, 'MID_FLAG_TABLE 解析不到任何項目'
    return {mid: {'en': en.replace("\\'", "'"), 'zh': zh}
            for mid, en, zh in entries}


def test_json_matches_js_entry_for_entry(json_table, js_table):
    assert json_table == js_table


def test_covers_top10_flag_mids_and_cn_tw(json_table):
    for mid in TOP_10_FLAG_MIDS | {'412', '413', '414', '416'}:
        assert mid in json_table, f'MID {mid} 缺席'
    assert json_table['412']['zh'] == '中國'
    assert json_table['416']['zh'] == '台灣'


def test_flag_for_mmsi_lookup_and_fallback(json_table):
    f = agg.flag_for_mmsi('412345678', json_table)
    assert f == {'mid': '412', 'en': 'China', 'zh': '中國'}
    # 查無 MID
    f = agg.flag_for_mmsi('999999999', json_table)
    assert f['en'] == 'Unknown' and f['mid'] == '999'
    # 過短 / 空
    assert agg.flag_for_mmsi('9', json_table)['en'] == 'Unknown'
    assert agg.flag_for_mmsi(None, json_table)['zh'] == '未知'
