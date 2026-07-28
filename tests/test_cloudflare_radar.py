"""Cloudflare Radar 流量異常偵測的純函式測試。

不打網路：時間序列全部合成，API 回應用假 session 餵。
"""
import math
from datetime import datetime, timedelta, timezone

import pytest

import random

from fetch_cloudflare_radar import (
    SERIES_SPECS,
    _get_env,
    _near_cable_km,
    analyze_series,
    build_summary,
    mask_reporting_gaps,
    build_cable_index,
    classify_severity,
    correlate_with_vessels,
    detect_anomalies,
    effective_scale,
    fetch_series,
    hour_of_week_baseline,
    parse_timeseries_payload,
    robust_scale,
    trim_series_for_output,
)

UTC = timezone.utc
START = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)


def _hourly_series(hours=28 * 24, start=START):
    """帶日／週週期的乾淨流量序列（模擬真實流量的作息曲線）。"""
    timestamps, values = [], []
    for h in range(hours):
        t = start + timedelta(hours=h)
        daily = 30 * math.sin((t.hour - 3) / 24 * 2 * math.pi)
        weekly = -8 if t.weekday() >= 5 else 0   # 週末低一點
        timestamps.append(t.isoformat().replace("+00:00", "Z"))
        values.append(100.0 + daily + weekly)
    return timestamps, values


def _inject_drop(values, start_index, hours, pct):
    out = list(values)
    for i in range(start_index, min(start_index + hours, len(out))):
        out[i] = out[i] * (1 - pct / 100.0)
    return out


# ── 回應解析 ────────────────────────────────────────────────────────────────

def test_parse_serie_0_with_string_values():
    """Radar 常以字串回傳數值。"""
    payload = {"success": True, "result": {
        "meta": {"aggInterval": "1h"},
        "serie_0": {"timestamps": ["2026-07-01T00:00:00Z", "2026-07-01T01:00:00Z"],
                    "values": ["1.5", "2.5"]},
    }}
    ts, vals = parse_timeseries_payload(payload)
    assert ts == ["2026-07-01T00:00:00Z", "2026-07-01T01:00:00Z"]
    assert vals == [1.5, 2.5]


def test_parse_iqi_prefers_p50():
    payload = {"result": {"serie_0": {
        "timestamps": ["2026-07-01T00:00:00Z"],
        "p25": ["10"], "p50": ["20"], "p75": ["30"],
    }}}
    assert parse_timeseries_payload(payload)[1] == [20.0]


def test_parse_handles_unknown_shape_and_empty():
    # 沒有已知值欄位 → 取長度相符的數值陣列
    payload = {"result": {"serie_x": {"timestamps": ["a", "b"], "foo": [1, 2]}}}
    assert parse_timeseries_payload(payload) == (["a", "b"], [1.0, 2.0])
    assert parse_timeseries_payload({"result": {}}) == ([], [])
    assert parse_timeseries_payload(None) == ([], [])


def test_parse_drops_nan_values():
    payload = {"result": {"serie_0": {
        "timestamps": ["2026-07-01T00:00:00Z", "2026-07-01T01:00:00Z"],
        "values": ["1.0", "not-a-number"],
    }}}
    assert parse_timeseries_payload(payload)[1] == [1.0, None]


class _FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeSession:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        return self.resp


def test_fetch_series_sends_bearer_and_params():
    payload = {"success": True, "result": {"serie_0": {
        "timestamps": ["2026-07-01T00:00:00Z"], "values": ["1"]}}}
    session = _FakeSession(_FakeResp(payload=payload))
    spec = {"id": "s", "label": "s", "path": "radar/http/timeseries",
            "params": {"location": "TW"}}
    out = fetch_series(spec, "tok123", days=14, session=session)
    assert out["values"] == [1.0]
    url, params, headers = session.calls[0]
    assert url.endswith("/radar/http/timeseries")
    assert params["dateRange"] == "14d" and params["location"] == "TW"
    assert params["aggInterval"] == "1h"
    assert headers["Authorization"] == "Bearer tok123"


def test_fetch_series_returns_none_on_error_status():
    session = _FakeSession(_FakeResp(status=403, text="forbidden"))
    spec = {"id": "s", "label": "s", "path": "radar/http/timeseries"}
    assert fetch_series(spec, "tok", session=session) is None


def test_fetch_series_returns_none_when_api_reports_failure():
    session = _FakeSession(_FakeResp(payload={"success": False, "errors": [{"code": 1}]}))
    spec = {"id": "s", "label": "s", "path": "radar/http/timeseries"}
    assert fetch_series(spec, "tok", session=session) is None


def test_token_env_aliases(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDEFARETOKEN", "  abc  ")
    assert _get_env("CLOUDFLARE_API_TOKEN", "CLAUDEFARETOKEN") == "abc"


# ── 基線 ────────────────────────────────────────────────────────────────────

def test_hour_of_week_baseline_tracks_daily_cycle():
    ts, vals = _hourly_series()
    baseline = hour_of_week_baseline(ts, vals)
    assert all(b is not None for b in baseline)
    # 乾淨資料上，基線應該幾乎等於觀測值
    assert max(abs(v - b) for v, b in zip(vals, baseline)) < 1e-6


def test_baseline_is_leave_one_out_so_anomaly_does_not_hide_itself():
    ts, vals = _hourly_series()
    drop_at = 20 * 24 + 5
    dirty = _inject_drop(vals, drop_at, 4, 40)
    baseline = hour_of_week_baseline(ts, dirty)
    # 掉點自己的基線仍然接近正常值（沒有被自己拉下去）
    assert baseline[drop_at] > dirty[drop_at] * 1.4


def test_baseline_falls_back_to_rolling_median_when_history_short():
    ts, vals = _hourly_series(hours=48)   # 不到兩週，沒有同時段樣本
    baseline = hour_of_week_baseline(ts, vals)
    assert baseline[0] is None            # 開頭沒有足夠歷史
    assert baseline[-1] is not None       # 尾端由滾動中位數補上


def test_robust_scale_ignores_single_outlier():
    base = [0.1, -0.1, 0.05, -0.05, 0.0, 0.02]
    assert robust_scale(base) == pytest.approx(robust_scale(base + [-500.0]), rel=0.5)
    assert robust_scale([0.0] * 10) is None   # 全零 → 無尺度可用
    assert robust_scale([1.0]) is None        # 樣本太少


def test_effective_scale_floors_on_baseline_when_series_is_too_regular():
    """MAD=0 時不能放棄偵測（否則極平穩的序列上大幅掉點會漏掉）。"""
    residuals = [0.0] * 20
    baseline = [100.0] * 20
    assert robust_scale(residuals) is None
    assert effective_scale(residuals, baseline) == pytest.approx(0.5)  # 100 × 0.5%


def test_effective_scale_uses_mad_when_series_is_noisy():
    residuals = [5.0, -5.0, 4.0, -4.0, 6.0, -6.0, 5.5, -5.5]
    baseline = [100.0] * 8
    # 雜訊尺度（≈7）遠大於下限 0.5 → 由 MAD 主導
    assert effective_scale(residuals, baseline) > 5.0


def test_effective_scale_none_when_no_signal_at_all():
    assert effective_scale([0.0] * 20, [0.0] * 20) is None


def test_no_false_alarm_on_noisy_but_normal_traffic():
    """真實流量有雜訊；±8% 的隨機抖動不該被判成異常。"""
    ts, vals = _hourly_series()
    rng = random.Random(42)
    noisy = [v * (1 + rng.uniform(-0.08, 0.08)) for v in vals]
    events = detect_anomalies(ts, noisy)
    assert events == [], f"誤報 {len(events)} 件: {events[:2]}"


def test_still_detects_cut_inside_noisy_series():
    ts, vals = _hourly_series()
    rng = random.Random(7)
    noisy = [v * (1 + rng.uniform(-0.08, 0.08)) for v in vals]
    dirty = _inject_drop(noisy, 22 * 24 + 3, 8, 50)
    events = detect_anomalies(ts, dirty)
    assert len(events) == 1
    assert events[0]["severity"] == "critical"


# ── 異常偵測 ────────────────────────────────────────────────────────────────

def test_no_anomaly_on_clean_seasonal_data():
    ts, vals = _hourly_series()
    assert detect_anomalies(ts, vals) == []


def test_detects_sustained_drop_as_critical():
    """海纜中斷的特徵：持續數小時的大幅下掉。"""
    ts, vals = _hourly_series()
    drop_at = 21 * 24 + 8
    dirty = _inject_drop(vals, drop_at, 6, 45)
    events = detect_anomalies(ts, dirty)
    assert len(events) == 1
    e = events[0]
    assert e["onset"] == ts[drop_at]
    assert e["duration_hours"] == 6
    assert e["severity"] == "critical"
    assert e["max_deviation_pct"] < -30
    assert e["peak_z"] < 0


def test_single_hour_dip_is_ignored_as_noise():
    ts, vals = _hourly_series()
    dirty = _inject_drop(vals, 21 * 24 + 8, 1, 60)   # 只有一個時段
    assert detect_anomalies(ts, dirty) == []


def test_small_deviation_below_pct_floor_is_ignored():
    """z 很大但幅度很小（低基數雜訊）不該成案。"""
    ts, vals = _hourly_series()
    dirty = _inject_drop(vals, 21 * 24 + 8, 5, 3)    # 只掉 3%
    assert detect_anomalies(ts, dirty) == []


def test_spike_direction_for_latency():
    ts, vals = _hourly_series()
    at = 21 * 24 + 2
    dirty = _inject_drop(vals, at, 5, -60)           # 負的掉幅 = 上衝
    assert detect_anomalies(ts, dirty, direction="drop") == []
    events = detect_anomalies(ts, dirty, direction="spike")
    assert len(events) == 1
    assert events[0]["max_deviation_pct"] > 30


def test_separate_runs_become_separate_events():
    ts, vals = _hourly_series()
    dirty = _inject_drop(vals, 20 * 24, 4, 40)
    dirty = _inject_drop(dirty, 24 * 24, 4, 40)
    assert len(detect_anomalies(ts, dirty)) == 2


def test_severity_thresholds():
    assert classify_severity({"max_deviation_pct": -45, "duration_hours": 5,
                              "peak_z": -9}) == "critical"
    # 幅度夠大但只持續 1 小時 → 不到 critical
    assert classify_severity({"max_deviation_pct": -45, "duration_hours": 1,
                              "peak_z": -4}) == "high"
    assert classify_severity({"max_deviation_pct": -20, "duration_hours": 2,
                              "peak_z": -3.5}) == "high"
    assert classify_severity({"max_deviation_pct": -11, "duration_hours": 2,
                              "peak_z": -3.1}) == "medium"


def test_all_series_use_alpha2_location_codes():
    """回歸測試：Radar 的 location 只吃 alpha-2 國家碼。

    曾經用 TW-LIE / TW-KIN 想拿馬祖、金門的離島粒度，Radar 回 HTTP 400
    （Invalid location codes）。Radar 沒有縣市粒度，別再加回來。
    """
    for spec in SERIES_SPECS:
        loc = spec.get("params", {}).get("location")
        if loc is None:
            continue
        assert "-" not in loc, f"{spec['id']}: location={loc} 不是 alpha-2 國家碼"
        assert len(loc) == 2, f"{spec['id']}: location={loc} 不是 alpha-2 國家碼"


def test_mask_reporting_gaps_turns_exact_zero_into_missing():
    assert mask_reporting_gaps([1.0, 0, 2.0, None, 0.0]) == [1.0, None, 2.0, None, None]


def test_zero_valued_reporting_gap_does_not_become_an_anomaly():
    """首次真實執行的假警報：全台 HTTP 請求量連兩小時剛好 0 → -100%、z=-45.8。"""
    ts, vals = _hourly_series()
    at = 22 * 24 + 4
    dirty = list(vals)
    for i in range(at, at + 3):
        dirty[i] = 0.0                       # 回報缺口，不是真的沒有流量
    out = analyze_series({"id": "s", "label": "s", "direction": "drop"},
                         {"timestamps": ts, "values": dirty})
    assert out["anomalies"] == []
    assert out["reporting_gaps"] == 3


def test_genuine_deep_drop_still_detected_alongside_zero_masking():
    """把 0 當缺值不能連真事件一起擋掉（真實資料裡的 -75.9% 那類）。"""
    ts, vals = _hourly_series()
    dirty = _inject_drop(vals, 22 * 24 + 4, 4, 76)
    out = analyze_series({"id": "s", "label": "s", "direction": "drop"},
                         {"timestamps": ts, "values": dirty})
    assert len(out["anomalies"]) == 1
    assert out["anomalies"][0]["severity"] == "critical"
    assert out["reporting_gaps"] == 0


# ── 關聯涵蓋範圍 ────────────────────────────────────────────────────────────

def test_event_older_than_ais_tracks_is_marked_not_silently_empty():
    """AIS 軌跡只留 14 天、Radar 視窗 28 天：更早的異常比不到船。

    「比不到」和「比對過但沒有嫌疑船」是完全不同的結論，不能都回空陣列。
    """
    onset = datetime(2026, 7, 9, 7, tzinfo=UTC)          # 早於軌跡
    entries = [_entry(datetime(2026, 7, 20, 12, tzinfo=UTC), [_vessel()])]
    out = correlate_with_vessels([{"onset": onset.isoformat().replace("+00:00", "Z")}],
                                 entries, build_cable_index(_CABLE),
                                 port_lookup=lambda la, lo: None)[0]
    assert out["correlation_coverage"] == "outside_ais_window"
    assert out["correlated_vessels"] == []
    assert out["candidate_summary"]["total"] == 0


def test_event_inside_track_window_is_marked_ok():
    onset = datetime(2026, 7, 20, 12, tzinfo=UTC)
    entries = [_entry(onset - timedelta(hours=3), [_vessel()])]
    out = correlate_with_vessels([{"onset": onset.isoformat().replace("+00:00", "Z")}],
                                 entries, build_cable_index(_CABLE),
                                 port_lookup=lambda la, lo: None)[0]
    assert out["correlation_coverage"] == "ok"
    assert len(out["correlated_vessels"]) == 1


def test_summary_counts_uncorrelatable_events():
    old = {"onset": "2026-07-09T07:00:00Z", "severity": "critical"}
    entries = [_entry(datetime(2026, 7, 20, 12, tzinfo=UTC), [_vessel()])]
    events = correlate_with_vessels([old], entries, build_cable_index(_CABLE),
                                    port_lookup=lambda la, lo: None)
    s = build_summary([{"id": "s", "anomalies": events}], [])
    assert s["anomalies_outside_ais_window"] == 1
    assert s["anomalies_with_commercial_or_gov_candidates"] == 0


def test_trim_series_keeps_recent_arrays_and_all_anomalies():
    """檔案每 2 小時被重寫提交一次，原始陣列要裁短，但偵測結果不能動。"""
    ts, vals = _hourly_series()                      # 28 天
    spec = {"id": "tw", "label": "TW", "direction": "drop"}
    dirty = _inject_drop(vals, 5 * 24, 6, 45)        # 異常落在會被裁掉的舊區間
    analyzed = [analyze_series(spec, {"timestamps": ts, "values": dirty})]
    assert len(analyzed[0]["anomalies"]) == 1

    out = trim_series_for_output(analyzed, retain_days=14)[0]
    assert len(out["timestamps"]) == 14 * 24
    assert len(out["values"]) == 14 * 24
    assert len(out["baseline"]) == 14 * 24
    assert out["timestamps"][-1] == ts[-1]           # 保留的是「最近」的
    assert out["points"] == len(ts)                  # 偵測用的點數原樣保留
    assert out["series_retained_days"] == 14
    assert len(out["anomalies"]) == 1                # 舊異常事件仍完整保留


def test_trim_series_does_not_mutate_input():
    ts, vals = _hourly_series()
    analyzed = [analyze_series({"id": "tw", "label": "TW", "direction": "drop"},
                               {"timestamps": ts, "values": vals})]
    before = len(analyzed[0]["timestamps"])
    trim_series_for_output(analyzed, retain_days=7)
    assert len(analyzed[0]["timestamps"]) == before


def test_trim_series_rounds_values():
    series = [{"id": "s", "timestamps": ["t1", "t2"],
               "values": [1.23456789, None], "baseline": [9.87654321, 2.0],
               "anomalies": []}]
    out = trim_series_for_output(series, retain_days=14, precision=2)[0]
    assert out["values"] == [1.23, None]
    assert out["baseline"] == [9.88, 2.0]


def test_analyze_series_reports_baseline_coverage():
    ts, vals = _hourly_series()
    spec = {"id": "tw", "label": "TW", "direction": "drop"}
    out = analyze_series(spec, {"timestamps": ts, "values": vals})
    assert out["id"] == "tw"
    assert out["points"] == len(vals)
    assert out["baseline_coverage"] == 1.0
    assert out["anomalies"] == []


# ── 海纜格網 + 船隻關聯 ─────────────────────────────────────────────────────

# 一條沿 24.5N 的假海纜
_CABLE = [{"points": [(24.5, 120.0), (24.5, 121.0)]}]


def test_cable_index_finds_points_near_cable_and_rejects_far_ones():
    index = build_cable_index(_CABLE)
    assert _near_cable_km(24.5, 120.5, index) == pytest.approx(0, abs=0.2)
    near = _near_cable_km(24.52, 120.5, index)
    assert near is not None and near < 5.0
    assert _near_cable_km(23.0, 120.5, index) is None   # 遠離海纜
    assert _near_cable_km(24.5, 125.0, index) is None   # 不在海纜經度範圍


def _entry(ts, vessels):
    return {"timestamp": ts.isoformat().replace("+00:00", "Z"), "vessels": vessels}


def _vessel(mmsi="412000001", name="SHIP A", lat=24.5, lon=120.5, speed=1.0,
            type_name="cargo"):
    return {"mmsi": mmsi, "name": name, "lat": lat, "lon": lon,
            "speed": speed, "type_name": type_name}


def test_correlation_finds_loitering_vessel_before_onset():
    onset = datetime(2026, 7, 20, 12, tzinfo=UTC)
    entries = [
        _entry(onset - timedelta(hours=30), [_vessel()]),          # 太早
        _entry(onset - timedelta(hours=6), [_vessel(speed=0.5)]),  # 命中
        _entry(onset - timedelta(hours=4),
               [_vessel(lat=24.52, speed=2.0),                      # 命中（同一艘）
                _vessel(mmsi="412000002", name="FAST", speed=14.0), # 太快
                _vessel(mmsi="412000003", name="FAR", lat=22.0)]),  # 離海纜太遠
        _entry(onset + timedelta(hours=2), [_vessel(mmsi="412000009")]),  # 異常之後
    ]
    events = [{"onset": onset.isoformat().replace("+00:00", "Z")}]
    out = correlate_with_vessels(events, entries, build_cable_index(_CABLE))
    vessels = out[0]["correlated_vessels"]
    assert [v["mmsi"] for v in vessels] == ["412000001"]
    assert vessels[0]["points"] == 2          # 窗內出現兩次
    assert vessels[0]["nearest_cable_km"] < 1  # 取最靠近海纜的那一筆


def test_correlation_excludes_fishing_net_beacons():
    """海纜旁最密的低速目標是漁網信標（船名帶 %），必須沿用既有排除規則。"""
    onset = datetime(2026, 7, 20, 12, tzinfo=UTC)
    entries = [_entry(onset - timedelta(hours=3), [
        _vessel(mmsi="61118004", name="MINDONGYU61118-4-92%", type_name="unknown"),
        _vessel(mmsi="898123456", name="NET MARKER", type_name="unknown"),
        _vessel(mmsi="987654321", name="ATON", type_name="unknown"),
        _vessel(mmsi="412000010", name="REAL SHIP", type_name="cargo"),
    ])]
    events = [{"onset": onset.isoformat().replace("+00:00", "Z")}]
    out = correlate_with_vessels(events, entries, build_cable_index(_CABLE),
                                 port_lookup=lambda la, lo: None)
    assert [v["mmsi"] for v in out[0]["correlated_vessels"]] == ["412000010"]


def test_correlation_ranks_commercial_above_fishing_even_when_further():
    onset = datetime(2026, 7, 20, 12, tzinfo=UTC)
    entries = [_entry(onset - timedelta(hours=3), [
        # 漁船貼在海纜上，貨輪離 4km —— 貨輪仍應排前面
        _vessel(mmsi="412000001", name="FISHER", lat=24.5, type_name="fishing"),
        _vessel(mmsi="412000002", name="BULKER", lat=24.536, type_name="cargo"),
        _vessel(mmsi="412000003", name="CCG", lat=24.53, type_name="coastguard"),
    ])]
    events = [{"onset": onset.isoformat().replace("+00:00", "Z")}]
    out = correlate_with_vessels(events, entries, build_cable_index(_CABLE),
                                 port_lookup=lambda la, lo: None)
    vessels = out[0]["correlated_vessels"]
    assert [v["name"] for v in vessels] == ["BULKER", "CCG", "FISHER"]
    assert out[0]["candidate_summary"] == {"commercial": 1, "gov": 1,
                                           "other": 1, "total": 3}


def test_correlation_skips_in_port_positions():
    """海纜登陸點就在港邊，靠泊的船必然「0 節、離海纜很近」。"""
    onset = datetime(2026, 7, 20, 12, tzinfo=UTC)
    entries = [_entry(onset - timedelta(hours=3), [
        _vessel(mmsi="412000001", name="BERTHED", lon=120.2, type_name="cargo"),
        _vessel(mmsi="412000002", name="AT SEA", lon=120.8, type_name="cargo"),
    ])]
    events = [{"onset": onset.isoformat().replace("+00:00", "Z")}]
    out = correlate_with_vessels(
        events, entries, build_cable_index(_CABLE),
        port_lookup=lambda la, lo: "港口" if lo < 120.5 else None)
    assert [v["name"] for v in out[0]["correlated_vessels"]] == ["AT SEA"]


def test_candidate_summary_counts_all_candidates_not_just_the_listed_ones():
    onset = datetime(2026, 7, 20, 12, tzinfo=UTC)
    many = [_vessel(mmsi=f"41200{i:04d}", name=f"F{i}", type_name="fishing")
            for i in range(15)]
    entries = [_entry(onset - timedelta(hours=2), many)]
    events = [{"onset": onset.isoformat().replace("+00:00", "Z")}]
    out = correlate_with_vessels(events, entries, build_cable_index(_CABLE),
                                 max_vessels=3,
                                 port_lookup=lambda la, lo: None)[0]
    assert len(out["correlated_vessels"]) == 3      # 名單截斷
    assert out["candidate_summary"]["total"] == 15  # 計數不截斷
    assert out["candidate_summary"]["other"] == 15


def test_correlation_returns_empty_list_when_nothing_matches():
    onset = datetime(2026, 7, 20, 12, tzinfo=UTC)
    events = [{"onset": onset.isoformat().replace("+00:00", "Z")}]
    out = correlate_with_vessels(events, [], build_cable_index(_CABLE))
    assert out[0]["correlated_vessels"] == []


def test_correlation_survives_bad_onset_timestamp():
    out = correlate_with_vessels([{"onset": "not-a-date"}], [],
                                 build_cable_index(_CABLE))
    assert out[0]["correlated_vessels"] == []


def test_correlation_preserves_original_event_fields():
    onset = datetime(2026, 7, 20, 12, tzinfo=UTC)
    event = {"onset": onset.isoformat().replace("+00:00", "Z"),
             "severity": "critical", "peak_z": -8.1}
    out = correlate_with_vessels([event], [], build_cable_index(_CABLE))
    assert out[0]["severity"] == "critical" and out[0]["peak_z"] == -8.1
    assert "correlated_vessels" in out[0]
