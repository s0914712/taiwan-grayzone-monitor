"""Cloudflare Radar 縣市（ADM1）級指標的純函式測試（不打網路）。

沙箱連不到 api.cloudflare.com，因此這裡驗的是**與網路無關**的部分：名稱／ISO
比對、指標階梯的降級順序、Speed Test 摘要解析、輸出精簡、色階等級判定。
回應形狀取自 Actions run 33216931232 的實測（台北 geoId 7280290）。
"""
from datetime import datetime, timedelta, timezone

import pytest

import fetch_radar_counties as M

UTC = timezone.utc


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Session:
    """依 path + params 決定回什麼，用來模擬「某些端點不吃 geoId」。"""

    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.responder(url, params or {})


def _timeseries_payload(values, start="2026-08-01T00:00:00Z"):
    base = datetime(2026, 8, 1, tzinfo=UTC)
    return {"success": True, "result": {"serie_0": {
        "timestamps": [(base + timedelta(hours=i)).isoformat().replace("+00:00", "Z")
                       for i in range(len(values))],
        "values": [str(v) for v in values],
    }}}


# ── 名冊 ────────────────────────────────────────────────────────────────────

def test_roster_covers_all_22_counties():
    roster = M.county_roster()
    assert len(roster) == 22
    assert {c["iso"] for c in roster} >= {"TW-LIE", "TW-KIN", "TW-PEN", "TW-TPE"}


# ── 分區（ADM1）比對 ────────────────────────────────────────────────────────
# 實測（run 33239842250）：Radar 的台灣 ADM1 只有 4 個分區，entity 沒有 ISO 欄位，
# 名稱是 GeoNames 的舊分區名（Takao＝高雄、Fukien＝金門＋馬祖）。

GEOLOCATIONS_ENTITIES = [
    {"geo_id": "7280288", "name": "Fukien", "type": "ADM1"},
    {"geo_id": "1668284", "name": "Taiwan", "type": "COUNTRY"},
    {"geo_id": "6255147", "name": "Asia", "type": "CONTINENT"},
    {"geo_id": "7280291", "name": "Taiwan", "type": "ADM1"},
    {"geo_id": "7280289", "name": "Takao", "type": "ADM1"},
    {"geo_id": "7280290", "name": "Taipei", "type": "ADM1"},
]


def test_groups_cover_all_22_counties_without_overlap():
    members = [iso for g in M.RADAR_ADM1_GROUPS for iso in g["members"]]
    assert len(members) == len(set(members)), "同一個縣市不能屬於兩個分區"
    assert set(members) == set(M.COUNTY_NAMES_ZH)


def test_fukien_group_is_kinmen_plus_matsu():
    """Radar 唯一單獨切出來的離島分區——馬祖在 IODA 沒有資料，這是它唯一的頻寬來源。"""
    fukien = next(g for g in M.RADAR_ADM1_GROUPS if g["id"] == "fukien")
    assert set(fukien["members"]) == {"TW-KIN", "TW-LIE"}


def test_match_group_geoids_reads_the_real_response():
    assert M.match_group_geoids(GEOLOCATIONS_ENTITIES) == {
        "fukien": "7280288", "taiwan_province": "7280291",
        "takao": "7280289", "taipei": "7280290",
    }


def test_country_entity_never_becomes_the_province_group():
    """回應裡「Taiwan」出現兩次：COUNTRY 1668284 與臺灣省 ADM1 7280291。

    不看 type 就會把整個國家的數值當成臺灣省分區畫上去。
    """
    out = M.match_group_geoids([
        {"geo_id": "1668284", "name": "Taiwan", "type": "COUNTRY"},
        {"geo_id": "7280291", "name": "Taiwan", "type": "ADM1"},
    ])
    assert out["taiwan_province"] == "7280291"


def test_match_group_geoids_knows_the_old_geonames_names():
    assert M.match_group_geoids(
        [{"geo_id": "9", "name": "Kaohsiung", "type": "ADM1"}]) == {"takao": "9"}
    assert M.match_group_geoids(
        [{"geo_id": "8", "name": "Fujian", "type": "ADM1"}]) == {"fukien": "8"}


def test_match_group_geoids_drops_unrelated_entities():
    assert M.match_group_geoids([{"geo_id": "1", "name": "Tokyo", "type": "ADM1"}]) == {}
    assert M.match_group_geoids([{"name": "Taipei", "type": "ADM1"}]) == {}


def test_extract_adm1_entities_keeps_the_type_field():
    payload = {"result": {"geolocations": [
        {"geoId": "7280290", "name": "Taipei", "type": "ADM1", "code": "03"}]}}
    entities = M.extract_adm1_entities(payload)
    assert entities[0]["type"] == "ADM1" and entities[0]["geo_id"] == "7280290"


def test_static_group_geoids_are_the_probe_verified_ids():
    assert M.static_group_geoids() == {
        "taipei": "7280290", "takao": "7280289",
        "fukien": "7280288", "taiwan_province": "7280291",
    }


# ── 指標階梯 ────────────────────────────────────────────────────────────────

def test_ladder_falls_back_to_next_metric_on_400():
    def responder(url, params):
        if "iqi" in url:
            return _Resp(400)                       # 速度類端點不吃 geoId
        return _Resp(200, _timeseries_payload([10, 11, 12]))

    metric, series, attempts = M.fetch_group_metric(_Session(responder), "tok", "42")
    assert metric["id"] == "netflows_traffic"
    assert series["values"] == [10.0, 11.0, 12.0]
    assert [a["ok"] for a in attempts] == [False, False, True]


def test_ladder_prefers_bandwidth_when_available():
    responder = lambda url, params: _Resp(200, _timeseries_payload([88, 90]))
    metric, _, attempts = M.fetch_group_metric(_Session(responder), "tok", "42")
    assert metric["id"] == "iqi_bandwidth"
    assert metric["is_speed"] and metric["unit"] == "Mbps"
    assert len(attempts) == 1, "第一個成功就該停手，不要多打請求"


def test_ladder_returns_none_when_everything_fails():
    metric, series, attempts = M.fetch_group_metric(
        _Session(lambda url, params: _Resp(403)), "tok", "42")
    assert metric is None and series is None
    assert len(attempts) == len(M.METRIC_LADDER)


def test_empty_series_counts_as_unavailable():
    """HTTP 200 但整條都是 null（樣本不足）不能算「有資料」。"""
    payload = _timeseries_payload([1, 2])
    payload["result"]["serie_0"]["values"] = [None, None]
    series, status = M.fetch_metric_series(
        _Session(lambda url, params: _Resp(200, payload)), "tok", "42",
        M.METRIC_LADDER[0])
    assert series is None and status == 200


def test_geoid_is_sent_on_every_request():
    session = _Session(lambda url, params: _Resp(200, _timeseries_payload([1, 2])))
    M.fetch_group_metric(session, "tok", "1668341")
    assert session.calls[0][1]["geoId"] == "1668341"


def test_latency_metric_detects_spikes_not_drops():
    latency = next(m for m in M.METRIC_LADDER if m["id"] == "iqi_latency")
    assert latency["direction"] == "spike"
    assert latency["higher_is_better"] is False


def test_traffic_index_is_not_labelled_as_speed():
    """流量指數不是網速——前端靠這個旗標決定要不要放進「網速」模式。"""
    netflows = next(m for m in M.METRIC_LADDER if m["id"] == "netflows_traffic")
    assert netflows["is_speed"] is False


# ── 輸出組裝 ────────────────────────────────────────────────────────────────

def test_downsample_takes_median_per_bucket():
    ts = [f"2026-08-01T{h:02d}:00:00Z" for h in range(6)]
    out_ts, out_vals = M.downsample_series(ts, [1, 5, 3, 10, 20, 30],
                                           bucket_hours=3, retain_days=7)
    assert out_ts == [ts[0], ts[3]]
    assert out_vals == [3, 20]


def test_downsample_bucket_of_only_gaps_is_none():
    ts = [f"2026-08-01T{h:02d}:00:00Z" for h in range(3)]
    _, vals = M.downsample_series(ts, [None, None, None], bucket_hours=3)
    assert vals == [None]


def test_downsample_trims_to_retention_window():
    hours = 24 * 14
    base = datetime(2026, 8, 1, tzinfo=UTC)
    ts = [(base + timedelta(hours=i)).isoformat() for i in range(hours)]
    out_ts, _ = M.downsample_series(ts, list(range(hours)), bucket_hours=3,
                                    retain_days=7)
    assert len(out_ts) == 7 * 24 / 3
    assert out_ts[0] == ts[hours - 7 * 24]


def test_latest_reading_skips_trailing_gaps():
    analysis = {"values": [10, 20, None], "baseline": [10, 25, 25]}
    value, baseline, pct = M.latest_reading(analysis)
    assert (value, baseline) == (20, 25)
    assert pct == -20.0


def test_latest_reading_handles_all_missing():
    assert M.latest_reading({"values": [None], "baseline": [None]}) == (None, None, None)


def test_classify_level_ongoing_vs_recent():
    now = datetime(2026, 8, 28, 12, tzinfo=UTC)
    ongoing = [{"onset": "2026-08-28T09:00:00Z", "end": "2026-08-28T11:00:00Z"}]
    old = [{"onset": "2026-08-20T09:00:00Z", "end": "2026-08-20T11:00:00Z"}]
    assert M.classify_level(ongoing, now=now) == "alert"
    assert M.classify_level(old, now=now) == "watch"
    assert M.classify_level([], now=now) == "normal"


FUKIEN = next(g for g in M.RADAR_ADM1_GROUPS if g["id"] == "fukien")


def test_build_group_record_carries_metric_provenance():
    analysis = {"values": [90, 80], "baseline": [100, 100],
                "timestamps": ["2026-08-01T00:00:00Z", "2026-08-01T01:00:00Z"],
                "points": 2, "baseline_coverage": 1.0, "anomalies": []}
    rec = M.build_group_record(FUKIEN, M.METRIC_LADDER[0], analysis,
                               geo_id="7280288")
    assert rec["metric_id"] == "iqi_bandwidth" and rec["unit"] == "Mbps"
    assert rec["is_speed"] is True and rec["geo_id"] == "7280288"
    assert rec["latest"] == 80 and rec["pct_vs_baseline"] == -20.0
    assert rec["level"] == "normal" and rec["status"] == "available"
    assert rec["members"] == ["TW-KIN", "TW-LIE"]


def test_unavailable_group_record_is_unknown_not_normal():
    """沒有資料的分區在地圖上必須是灰色——畫成綠色等於謊報正常。"""
    rec = M.unavailable_group_record(FUKIEN, "no_metric_available")
    assert rec["level"] == "unknown" and rec["status"] == "unavailable"
    assert rec["metric_id"] is None


def test_county_records_are_marked_as_group_values():
    """22 筆縣市記錄裡的數字全部來自分區，每一筆都必須自己說清楚。"""
    analysis = {"values": [30], "baseline": [35],
                "timestamps": ["2026-08-29T00:00:00Z"], "points": 1,
                "baseline_coverage": 1.0, "anomalies": []}
    groups = [M.build_group_record(FUKIEN, M.METRIC_LADDER[0], analysis,
                                   geo_id="7280288")]
    counties = M.county_records_from_groups(groups)
    assert len(counties) == 22
    by_iso = {c["iso"]: c for c in counties}
    for iso in ("TW-KIN", "TW-LIE"):
        assert by_iso[iso]["is_group_value"] is True
        assert by_iso[iso]["latest"] == 30
        assert by_iso[iso]["adm1_group_id"] == "fukien"
        assert "金門" in by_iso[iso]["adm1_group_label_zh"]
    # 沒有被抓到的分區 → 灰色，不是「正常」
    assert by_iso["TW-TPE"]["level"] == "unknown"
    assert by_iso["TW-TPE"]["is_group_value"] is False


def test_county_records_do_not_inherit_the_group_identity_fields():
    """縣市記錄不能帶著分區的 label/members，那會讓人以為那是縣市自己的身分。"""
    analysis = {"values": [30], "baseline": [30],
                "timestamps": ["2026-08-29T00:00:00Z"], "points": 1,
                "baseline_coverage": 1.0, "anomalies": []}
    groups = [M.build_group_record(FUKIEN, M.METRIC_LADDER[0], analysis)]
    kinmen = next(c for c in M.county_records_from_groups(groups)
                  if c["iso"] == "TW-KIN")
    for key in ("group_id", "radar_name", "label_zh", "label_en", "members"):
        assert key not in kinmen
    assert kinmen["name_zh"] == "金門縣"


def test_summary_counts_groups_not_counties():
    """資料只有 4 個分區，「22 縣市裡有幾個有網速」是誤導性的數字。"""
    groups = [
        {"group_id": "taipei", "status": "available", "level": "normal",
         "metric_id": "iqi_bandwidth", "is_speed": True, "anomalies": []},
        {"group_id": "fukien", "status": "available", "level": "alert",
         "metric_id": "netflows_traffic", "is_speed": False,
         "anomalies": [{"onset": "2026-08-28T00:00:00Z"}]},
        {"group_id": "takao", "status": "unavailable", "level": "unknown",
         "metric_id": None, "anomalies": []},
    ]
    counties = [{"iso": "TW-TPE", "is_group_value": True},
                {"iso": "TW-CHA", "is_group_value": False}]
    summary = M.build_summary(groups, counties)
    assert summary["adm1_groups_total"] == 3
    assert summary["adm1_groups_with_data"] == 2
    assert summary["adm1_groups_with_speed_metric"] == 1
    assert summary["counties_total"] == 2
    assert summary["counties_covered_by_group"] == 1
    assert "counties_with_speed_metric" not in summary
    assert summary["metric_availability"] == {"iqi_bandwidth": 1,
                                              "netflows_traffic": 1}
    assert summary["latest_anomaly_onset"] == "2026-08-28T00:00:00Z"


# ── geoId 快取 ──────────────────────────────────────────────────────────────

def test_geoid_cache_is_used_when_fresh(tmp_path):
    cache = tmp_path / "geoids.json"
    cache.write_text('{"resolved_at": "%s", "groups": {"taipei": "5"}}'
                     % datetime.now(UTC).isoformat(), encoding="utf-8")
    session = _Session(lambda url, params: pytest.fail("快取有效時不該打 API"))
    out = M.resolve_group_geoids(session, "tok", cache_path=cache)
    assert out["taipei"] == "5"
    # 快取只覆蓋部分分區時，其餘沿用實測驗證過的靜態值
    assert out["fukien"] == "7280288"


def test_stale_cache_is_refreshed_from_api(tmp_path):
    cache = tmp_path / "geoids.json"
    stale = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    cache.write_text('{"resolved_at": "%s", "groups": {"taipei": "old"}}' % stale,
                     encoding="utf-8")
    payload = {"success": True, "result": {"geolocations": GEOLOCATIONS_ENTITIES}}
    out = M.resolve_group_geoids(_Session(lambda url, params: _Resp(200, payload)),
                                 "tok", cache_path=cache)
    assert out["taipei"] == "7280290" and out["fukien"] == "7280288"


def test_resolution_failure_falls_back_to_probe_verified_ids(tmp_path):
    """API 掛掉時退回實測驗證過的靜態表，比整張地圖變灰好。"""
    out = M.resolve_group_geoids(_Session(lambda url, params: _Resp(500)),
                                 "tok", cache_path=tmp_path / "missing.json")
    assert out == M.static_group_geoids()


# ── Speed Test 實測摘要（形狀取自 run 33216931232 的實測回應）────────────────

SPEED_SUMMARY_PAYLOAD = {"success": True, "result": {"summary_0": {
    "bandwidthDownload": "124.512334", "bandwidthUpload": "65.248269",
    "latencyIdle": "18.4", "latencyLoaded": "42.7",
    "jitterIdle": "2.13", "jitterLoaded": "9.8", "packetLoss": "0.0",
}}}


def test_parse_speed_summary_reads_string_numbers():
    out = M.parse_speed_summary(SPEED_SUMMARY_PAYLOAD)
    assert out["bandwidth_download"] == 124.51
    assert out["bandwidth_upload"] == 65.25
    assert out["latency_idle"] == 18.4 and out["jitter_idle"] == 2.13


def test_parse_speed_summary_tolerates_missing_fields():
    payload = {"result": {"summary_0": {"bandwidthDownload": "50"}}}
    assert M.parse_speed_summary(payload) == {"bandwidth_download": 50.0}


def test_parse_speed_summary_returns_none_without_bandwidth():
    assert M.parse_speed_summary({"result": {"summary_0": {"latencyIdle": "5"}}}) is None
    assert M.parse_speed_summary({"result": {}}) is None
    assert M.parse_speed_summary(None) is None


def test_fetch_speed_test_sends_geoid():
    session = _Session(lambda url, params: _Resp(200, SPEED_SUMMARY_PAYLOAD))
    out = M.fetch_speed_test(session, "tok", "7280290")
    assert out["bandwidth_download"] == 124.51
    assert session.calls[0][1]["geoId"] == "7280290"
    assert "quality/speed/summary" in session.calls[0][0]


def test_fetch_speed_test_failure_does_not_raise():
    assert M.fetch_speed_test(_Session(lambda url, params: _Resp(500)),
                              "tok", "1") is None


def test_speed_test_rides_along_but_does_not_drive_the_colour_scale():
    """色階要的是有基線的時間序列；Speed Test 只是 popup 的補充數字。

    兩者量級差很大（台北實測 IQI p50 14.3 Mbps vs Speed Test 下載 124.5 Mbps），
    混用會讓地圖上的數字跟圖例對不起來。
    """
    taipei = next(g for g in M.RADAR_ADM1_GROUPS if g["id"] == "taipei")
    analysis = {"values": [14.3], "baseline": [14.0],
                "timestamps": ["2026-08-28T00:00:00Z"], "points": 1,
                "baseline_coverage": 1.0, "anomalies": []}
    rec = M.build_group_record(taipei, M.METRIC_LADDER[0], analysis,
                               geo_id="7280290",
                               speed_test={"bandwidth_download": 124.51})
    assert rec["latest"] == 14.3, "色階仍用 IQI 序列的最後一筆"
    assert rec["speed_test"]["bandwidth_download"] == 124.51


def test_unavailable_group_record_carries_null_speed_test():
    assert M.unavailable_group_record(FUKIEN, "no_metric_available")["speed_test"] is None


def test_summary_counts_speed_test_coverage():
    groups = [
        {"group_id": "taipei", "status": "available", "level": "normal",
         "metric_id": "iqi_bandwidth", "is_speed": True, "anomalies": [],
         "speed_test": {"bandwidth_download": 124.5}},
        {"group_id": "takao", "status": "available", "level": "normal",
         "metric_id": "iqi_bandwidth", "is_speed": True, "anomalies": [],
         "speed_test": None},
    ]
    assert M.build_summary(groups, [])["adm1_groups_with_speed_test"] == 1


# ── geoId 解析路徑（實測回填）──────────────────────────────────────────────

def test_geoid_lookups_drop_the_nonexistent_summary_v2_routes():
    """`radar/{http,netflows}/summary_v2` 實測回 400 `No route for that URI`。

    正確的維度端點是 `summary/{dimension}` 與 `timeseries_groups/{dimension}`；
    留著錯路徑只是每次執行都白打一次請求。
    """
    paths = [path for path, _ in M.GEOID_LOOKUPS]
    assert not any("summary_v2" in path for path in paths)
    assert "radar/http/summary/adm1" in paths


def test_geoid_lookups_drop_the_ignored_country_alpha2_param():
    """`countryAlpha2=TW` 實測被忽略（回 183 筆全球清單），別再送。"""
    for _, params in M.GEOID_LOOKUPS:
        assert "countryAlpha2" not in params
