"""Cloudflare Radar 縣市（ADM1）級指標的純函式測試（不打網路）。

沙箱連不到 api.cloudflare.com，而且「哪些端點吃 geoId」要靠
src/probe_radar_regions.py 在 Actions 裡實測，因此這裡驗的是**與回應形狀無關**
的部分：名稱／ISO 比對、指標階梯的降級順序、輸出精簡、色階等級判定。
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


# ── geoId 比對 ──────────────────────────────────────────────────────────────

def test_match_geoids_prefers_iso_code():
    entries = [{"geo_id": "1", "name": "Somewhere Else", "iso": "TW-TPE"}]
    assert M.match_geoids(entries) == {"TW-TPE": "1"}


def test_match_geoids_falls_back_to_normalized_name():
    entries = [{"geo_id": "7", "name": "Hualien County", "iso": None},
               {"geo_id": "8", "name": "Taipei", "iso": None}]
    out = M.match_geoids(entries)
    assert out["TW-HUA"] == "7"
    assert out["TW-TPE"] == "8"


def test_match_geoids_maps_matsu_aliases():
    for name in ("Matsu", "Lienchiang", "Lienkiang"):
        assert M.match_geoids([{"geo_id": "9", "name": name}]) == {"TW-LIE": "9"}


def test_match_geoids_drops_unrecognized_entities():
    # 對不上的實體寧可丟掉：把福建的數字畫到連江頭上比沒有資料更糟
    assert M.match_geoids([{"geo_id": "1", "name": "Fujian"}]) == {}
    assert M.match_geoids([{"name": "Taipei"}]) == {}


def test_extract_adm1_walks_unknown_response_shapes():
    payload = {"result": {"geolocations": [
        {"geoId": "123", "name": "Taipei", "iso3166Alpha2": "TW-TPE"}]}}
    assert M.extract_adm1_entities(payload) == [
        {"geo_id": "123", "name": "Taipei", "iso": "TW-TPE"}]

    nested = {"result": {"adm1": {"top": [{"id": "9", "name": "Penghu"}]}}}
    assert M.extract_adm1_entities(nested)[0]["name"] == "Penghu"


# ── 指標階梯 ────────────────────────────────────────────────────────────────

def test_ladder_falls_back_to_next_metric_on_400():
    def responder(url, params):
        if "iqi" in url:
            return _Resp(400)                       # 速度類端點不吃 geoId
        return _Resp(200, _timeseries_payload([10, 11, 12]))

    metric, series, attempts = M.fetch_county_metric(_Session(responder), "tok", "42")
    assert metric["id"] == "netflows_traffic"
    assert series["values"] == [10.0, 11.0, 12.0]
    assert [a["ok"] for a in attempts] == [False, False, True]


def test_ladder_prefers_bandwidth_when_available():
    responder = lambda url, params: _Resp(200, _timeseries_payload([88, 90]))
    metric, _, attempts = M.fetch_county_metric(_Session(responder), "tok", "42")
    assert metric["id"] == "iqi_bandwidth"
    assert metric["is_speed"] and metric["unit"] == "Mbps"
    assert len(attempts) == 1, "第一個成功就該停手，不要多打請求"


def test_ladder_returns_none_when_everything_fails():
    metric, series, attempts = M.fetch_county_metric(
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
    M.fetch_county_metric(session, "tok", "1668341")
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


def test_build_county_record_carries_metric_provenance():
    county = {"iso": "TW-LIE", "name_zh": "連江縣（馬祖）", "name_en": "Lienchiang (Matsu)"}
    metric = M.METRIC_LADDER[0]
    analysis = {"values": [90, 80], "baseline": [100, 100],
                "timestamps": ["2026-08-01T00:00:00Z", "2026-08-01T01:00:00Z"],
                "points": 2, "baseline_coverage": 1.0, "anomalies": []}
    rec = M.build_county_record(county, metric, analysis, geo_id="42")
    assert rec["metric_id"] == "iqi_bandwidth" and rec["unit"] == "Mbps"
    assert rec["is_speed"] is True and rec["geo_id"] == "42"
    assert rec["latest"] == 80 and rec["pct_vs_baseline"] == -20.0
    assert rec["level"] == "normal" and rec["status"] == "available"


def test_unavailable_record_is_unknown_not_normal():
    """沒有資料的縣市在地圖上必須是灰色——畫成綠色等於謊報正常。"""
    rec = M.unavailable_record({"iso": "TW-KIN", "name_zh": "金門縣",
                                "name_en": "Kinmen"}, "geoid_not_found")
    assert rec["level"] == "unknown" and rec["status"] == "unavailable"
    assert rec["metric_id"] is None


def test_summary_separates_speed_from_traffic_counties():
    counties = [
        {"iso": "A", "status": "available", "level": "normal",
         "metric_id": "iqi_bandwidth", "is_speed": True, "anomalies": []},
        {"iso": "B", "status": "available", "level": "alert",
         "metric_id": "netflows_traffic", "is_speed": False,
         "anomalies": [{"onset": "2026-08-28T00:00:00Z"}]},
        {"iso": "C", "status": "unavailable", "level": "unknown",
         "metric_id": None, "anomalies": []},
    ]
    summary = M.build_summary(counties)
    assert summary["counties_total"] == 3
    assert summary["counties_with_data"] == 2
    assert summary["counties_with_speed_metric"] == 1
    assert summary["metric_availability"] == {"iqi_bandwidth": 1,
                                              "netflows_traffic": 1}
    assert summary["by_level"]["unknown"] == 1
    assert summary["latest_anomaly_onset"] == "2026-08-28T00:00:00Z"


# ── geoId 快取 ──────────────────────────────────────────────────────────────

def test_geoid_cache_is_used_when_fresh(tmp_path):
    cache = tmp_path / "geoids.json"
    cache.write_text('{"resolved_at": "%s", "geoids": {"TW-TPE": "5"}}'
                     % datetime.now(UTC).isoformat(), encoding="utf-8")
    session = _Session(lambda url, params: pytest.fail("快取有效時不該打 API"))
    assert M.resolve_county_geoids(session, "tok", cache_path=cache) == {"TW-TPE": "5"}


def test_stale_cache_is_refreshed_from_api(tmp_path):
    cache = tmp_path / "geoids.json"
    stale = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    cache.write_text('{"resolved_at": "%s", "geoids": {"TW-TPE": "old"}}' % stale,
                     encoding="utf-8")
    payload = {"success": True, "result": {"geolocations": [
        {"geoId": "999", "name": "Taipei", "iso3166Alpha2": "TW-TPE"}]}}
    out = M.resolve_county_geoids(_Session(lambda url, params: _Resp(200, payload)),
                                  "tok", cache_path=cache)
    assert out == {"TW-TPE": "999"}


def test_stale_cache_survives_a_failed_refresh(tmp_path):
    """API 掛掉時沿用過期快取，比整張地圖變灰好。"""
    cache = tmp_path / "geoids.json"
    stale = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    cache.write_text('{"resolved_at": "%s", "geoids": {"TW-TPE": "old"}}' % stale,
                     encoding="utf-8")
    out = M.resolve_county_geoids(_Session(lambda url, params: _Resp(500)),
                                  "tok", cache_path=cache)
    assert out == {"TW-TPE": "old"}
