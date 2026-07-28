"""昨日中國公務船單日動態彙整（gov_daily_activity）的純函式測試。

不依賴真實 AIS 檔、基線檔或 matplotlib — 全部用合成 tier-1 snapshot entries。
"""
from datetime import datetime, timedelta, timezone

from gov_daily_activity import (
    TW_TZ,
    at_sea_only,
    category_counts,
    collect_daily_gov_activity,
    summarize_activity,
    tw_day_window,
)

UTC = timezone.utc


def _entry(ts, vessels):
    return {"timestamp": ts.isoformat(), "vessel_count": len(vessels), "vessels": vessels}


def _ccg(mmsi="413875048", name="CHINACOASTGUARD2204", lat=25.0, lon=122.0,
         speed=10.0, gov="coastguard"):
    v = {"mmsi": mmsi, "name": name, "lat": lat, "lon": lon, "speed": speed,
         "type_name": gov}
    if gov:
        v["gov"] = gov
    return v


def _day(entries_spec, day=datetime(2026, 7, 26, tzinfo=TW_TZ)):
    """建立指定台灣日期的 entries；entries_spec = [(小時, [vessels]), ...]"""
    return [_entry(day + timedelta(hours=h), vs) for h, vs in entries_spec]


# ── 時間視窗 ────────────────────────────────────────────────────────────────

def test_tw_day_window_is_previous_taiwan_calendar_day():
    now = datetime(2026, 7, 27, 8, 3, tzinfo=TW_TZ)  # 台灣早上 8 點推送
    start, end, label = tw_day_window(now=now, days_back=1)
    assert start == datetime(2026, 7, 26, tzinfo=TW_TZ)
    assert end == datetime(2026, 7, 27, tzinfo=TW_TZ)
    assert label == "2026/07/26"


def test_tw_day_window_accepts_utc_now_and_converts():
    # UTC 00:00 = 台灣 08:00 同一天 → 昨日仍是台灣 7/26
    now = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)
    start, _, label = tw_day_window(now=now, days_back=1)
    assert label == "2026/07/26"
    assert start.tzinfo.utcoffset(None) == timedelta(hours=8)


# ── 彙整 ────────────────────────────────────────────────────────────────────

def test_collect_filters_to_window():
    start, end, _ = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    entries = _day([(2, [_ccg()]), (10, [_ccg()])])
    # 前一天與當天各一筆，皆應被排除
    entries += [_entry(datetime(2026, 7, 25, 23, tzinfo=TW_TZ), [_ccg()]),
                _entry(datetime(2026, 7, 27, 1, tzinfo=TW_TZ), [_ccg()])]
    activity = collect_daily_gov_activity(entries, start, end)
    assert len(activity) == 1
    assert activity[0]["point_count"] == 2


def test_collect_skips_non_gov_and_aton_mmsi():
    start, end, _ = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    fishing = {"mmsi": "412448816", "name": "MINXIAYU08297", "lat": 26.9,
               "lon": 120.2, "speed": 0.1, "type_name": "fishing"}
    aton = _ccg(mmsi="900118637", name="HAIXUN08705", gov="msa")
    activity = collect_daily_gov_activity(
        _day([(3, [_ccg(), fishing, aton])]), start, end)
    assert [a["mmsi"] for a in activity] == ["413875048"]


def test_collect_computes_movement_metrics():
    start, end, _ = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    entries = _day([
        (4, [_ccg(lat=25.0, lon=122.0, speed=8.0)]),
        (6, [_ccg(lat=25.2, lon=122.0, speed=12.5)]),
        (8, [_ccg(lat=25.4, lon=122.0, speed=6.0)]),
    ])
    a = collect_daily_gov_activity(entries, start, end)[0]
    assert a["point_count"] == 3
    assert a["max_speed"] == 12.5
    assert a["last_lat"] == 25.4
    assert a["moving"] is True
    # 0.4 度緯度 ≈ 44 公里
    assert 40 < a["distance_km"] < 50
    assert a["first_seen"] < a["last_seen"]


def test_moored_vessel_is_not_moving():
    start, end, _ = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    entries = _day([(h, [_ccg(lat=24.42, lon=118.016, speed=0.0)])
                    for h in (2, 6, 10, 14)])
    a = collect_daily_gov_activity(entries, start, end)[0]
    assert a["moving"] is False
    assert a["distance_km"] < 1


def test_single_point_vessel_is_not_labelled_stationary():
    start, end, label = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    activity = collect_daily_gov_activity(
        _day([(15, [_ccg(speed=9.1)])]), start, end)
    a = activity[0]
    assert a["point_count"] == 1
    assert a["moving"] is False
    text = summarize_activity(activity, label)
    assert "僅 1 筆位置" in text
    assert "幾乎定點" not in text  # 只有一筆位置談不上定點


def test_name_variants_collapse_to_most_common():
    start, end, _ = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    entries = _day([
        (2, [_ccg(mmsi="413207080", name="HAIXUN07602", gov="msa")]),
        (4, [_ccg(mmsi="413207080", name="HAIXUN07602", gov="msa")]),
        (6, [_ccg(mmsi="413207080", name="HAIXUN 07602", gov="msa")]),
    ])
    a = collect_daily_gov_activity(entries, start, end)[0]
    assert a["name"] == "HAIXUN07602"
    assert a["point_count"] == 3


def test_category_from_type_name_when_gov_flag_missing():
    start, end, _ = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    v = _ccg(gov=None)
    v["type_name"] = "coastguard"
    activity = collect_daily_gov_activity(_day([(5, [v])]), start, end)
    assert activity[0]["category"] == "coastguard"


def test_sorted_coastguard_first_then_by_point_count():
    start, end, _ = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    entries = _day([
        (2, [_ccg(mmsi="1", gov="msa"), _ccg(mmsi="2", gov="coastguard"),
             _ccg(mmsi="3", gov="research")]),
        (4, [_ccg(mmsi="1", gov="msa")]),
    ])
    activity = collect_daily_gov_activity(entries, start, end)
    assert [a["category"] for a in activity] == ["coastguard", "msa", "research"]
    assert category_counts(activity) == {"coastguard": 1, "msa": 1, "research": 1}


# ── 靠港過濾（在港內的活動不報告）──────────────────────────────────────────

# 假的港口查詢：經度 <119 當作廈門港內，其餘視為海上
def _fake_port(lat, lon):
    return "廈門 Xiamen" if lon < 119.0 else None


def test_vessel_moored_all_day_is_not_at_sea():
    start, end, _ = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    entries = _day([(h, [_ccg(lat=24.42, lon=118.02, speed=0.0)])
                    for h in (2, 8, 14, 20)])
    a = collect_daily_gov_activity(entries, start, end, port_lookup=_fake_port)[0]
    assert a["at_sea"] is False
    assert a["in_port_points"] == 4
    assert a["port_name"] == "廈門 Xiamen"
    assert at_sea_only([a]) == []


def test_sortie_keeps_only_the_at_sea_leg():
    """在港 → 出海 → 回港：動態只算海上那段。"""
    start, end, _ = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    entries = _day([
        (2, [_ccg(lat=24.42, lon=118.02, speed=0.0)]),    # 港內
        (10, [_ccg(lat=24.30, lon=119.50, speed=12.0)]),  # 海上
        (14, [_ccg(lat=24.30, lon=120.00, speed=11.0)]),  # 海上
        (22, [_ccg(lat=24.42, lon=118.02, speed=0.0)]),   # 回港
    ])
    a = collect_daily_gov_activity(entries, start, end, port_lookup=_fake_port)[0]
    assert a["at_sea"] is True
    assert a["point_count"] == 2          # 只留海上兩點
    assert a["in_port_points"] == 2
    assert a["max_speed"] == 12.0         # 港內的 0 節不影響
    assert a["last_lon"] == 120.00        # 最新位置是海上那點，不是回港後的泊位
    assert a["moving"] is True


def test_summary_and_counts_exclude_in_port_vessels():
    start, end, label = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    entries = _day([(3, [
        _ccg(mmsi="413875053", name="CHINACOASTGUARD2103", lat=24.29, lon=119.22),
        _ccg(mmsi="413875051", name="CHINACOASTGUARD2101", lat=24.42, lon=118.02),
        _ccg(mmsi="413225690", name="HAIXUN0807", lat=24.50, lon=118.07, gov="msa"),
    ])])
    activity = collect_daily_gov_activity(entries, start, end, port_lookup=_fake_port)
    assert len(activity) == 3
    assert category_counts(at_sea_only(activity)) == {"coastguard": 1}

    text = summarize_activity(activity, label)
    assert "海警船共 1 艘在海上活動" in text
    assert "CHINACOASTGUARD2103" in text
    assert "CHINACOASTGUARD2101" not in text   # 停在廈門，不報告
    assert "海巡" not in text                   # 唯一的海巡也在港內
    assert "港內" not in text                   # 預設不提在港艘數


def test_summary_can_note_in_port_count_for_debugging():
    start, end, label = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    entries = _day([(3, [_ccg(lat=24.42, lon=118.02)])])
    activity = collect_daily_gov_activity(entries, start, end, port_lookup=_fake_port)
    text = summarize_activity(activity, label, note_in_port=True)
    assert "另有 1 艘整日停泊港內" in text


def test_summary_says_none_when_every_coastguard_is_in_port():
    start, end, label = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    entries = _day([(3, [_ccg(lat=24.42, lon=118.02)])])
    activity = collect_daily_gov_activity(entries, start, end, port_lookup=_fake_port)
    text = summarize_activity(activity, label)
    assert "未偵測到中國海警船在海上活動" in text


# ── 文字彙整 ────────────────────────────────────────────────────────────────

def test_summary_names_coastguard_and_counts_others():
    start, end, label = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    entries = _day([(3, [
        _ccg(mmsi="413875048", name="CHINACOASTGUARD2204"),
        _ccg(mmsi="413225690", name="HAIXUN0807", gov="msa"),
        _ccg(mmsi="413046020", name="DONGHAIJIU113", gov="rescue"),
    ])])
    text = summarize_activity(collect_daily_gov_activity(entries, start, end), label)
    assert "2026/07/26" in text
    assert "海警船共 1 艘" in text
    assert "CHINACOASTGUARD2204" in text
    # 非主角類別只報艘數，不點名
    assert "HAIXUN0807" not in text
    assert "海巡 1 艘" in text
    assert "海救 1 艘" in text


def test_summary_says_none_when_no_coastguard():
    start, end, label = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    entries = _day([(3, [_ccg(mmsi="413225690", name="HAIXUN0807", gov="msa")])])
    text = summarize_activity(collect_daily_gov_activity(entries, start, end), label)
    assert "未偵測到中國海警船" in text
    assert "海巡 1 艘" in text


def test_summary_handles_empty_activity():
    _, _, label = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    text = summarize_activity([], label)
    assert "未偵測到中國海警船" in text


def test_summary_truncates_long_coastguard_list():
    start, end, label = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    vessels = [_ccg(mmsi=f"41387500{i}", name=f"CHINACOASTGUARD{i}")
               for i in range(9)]
    activity = collect_daily_gov_activity(_day([(3, vessels)]), start, end)
    text = summarize_activity(activity, label, max_detail=6)
    assert "海警船共 9 艘" in text
    assert "另有 3 艘海警船未列出" in text


def test_summary_includes_zone_when_annotated():
    start, end, label = tw_day_window(now=datetime(2026, 7, 27, 8, tzinfo=TW_TZ))
    activity = collect_daily_gov_activity(_day([(3, [_ccg()])]), start, end)
    activity[0]["closest_zone"] = "contiguous_zone"
    activity[0]["closest_nm"] = 18.4
    text = summarize_activity(activity, label)
    assert "18.4 浬" in text
    assert "鄰接區" in text
