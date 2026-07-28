"""IODA 離島可達性監測的純函式測試（不打網路）。

entities/query 的回應形狀是實測抓回來的；signals/raw 的形狀沙箱連不上無法驗證，
因此 parse_signal_payload 寫成多層包裝容錯，這裡把已知的幾種形狀都測過。
"""
from datetime import datetime, timedelta, timezone

import pytest

import fetch_ioda as M
from anomaly_detect import analyze_values

UTC = timezone.utc


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Session:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.resp


# ── region 代碼解析（回應形狀為實測）──────────────────────────────────────

KINMEN_PAYLOAD = {
    "type": "entities.lookup",
    "data": [{
        "code": "4209", "name": "Kinmen", "type": "region", "subnames": [],
        "attrs": {"fqid": "geo.netacuity.AS.TW.4209", "country_code": "TW",
                  "country_name": "Taiwan Province Of China",
                  "ne_region_id": "3415"},
    }],
}


def test_resolve_region_code_from_real_payload_shape():
    session = _Session(_Resp(payload=KINMEN_PAYLOAD))
    assert M.resolve_region_code("Kinmen", session) == "4209"
    url, params = session.calls[0]
    assert url.endswith("/entities/query")
    assert params == {"entityType": "region", "search": "Kinmen"}


def test_resolve_region_code_skips_same_name_in_other_country():
    """同名地區可能出現在別的國家，必須挑台灣那個。"""
    payload = {"data": [
        {"code": "9999", "name": "Penghu", "attrs": {"country_code": "CN"}},
        {"code": "4211", "name": "Penghu", "attrs": {"country_code": "TW"}},
    ]}
    assert M.resolve_region_code("Penghu", _Session(_Resp(payload=payload))) == "4211"


def test_resolve_region_code_returns_none_when_absent():
    assert M.resolve_region_code("Nowhere", _Session(_Resp(payload={"data": []}))) is None
    assert M.resolve_region_code("X", _Session(_Resp(status=500, text="err"))) is None


# ── 訊號序列解析 ────────────────────────────────────────────────────────────

def test_parse_signal_builds_timestamps_from_start_and_step():
    """IODA 給的是起點＋step，不是逐點時間戳。"""
    start = int(datetime(2026, 7, 1, tzinfo=UTC).timestamp())
    payload = {"data": [[{"from": start, "step": 3600,
                          "values": [10, 11, None, 9]}]]}
    ts, vals = M.parse_signal_payload(payload)
    assert vals == [10.0, 11.0, None, 9.0]
    assert ts[0] == "2026-07-01T00:00:00Z"
    assert ts[1] == "2026-07-01T01:00:00Z"
    assert ts[3] == "2026-07-01T03:00:00Z"


def test_parse_signal_accepts_single_and_double_list_wrapping():
    start = int(datetime(2026, 7, 1, tzinfo=UTC).timestamp())
    node = {"from": start, "step": 3600, "values": [1, 2]}
    for payload in ({"data": [[node]]}, {"data": [node]}, {"data": [[[node]]]}):
        ts, vals = M.parse_signal_payload(payload)
        assert vals == [1.0, 2.0], payload


def test_parse_signal_handles_empty_and_malformed():
    assert M.parse_signal_payload({}) == ([], [])
    assert M.parse_signal_payload({"data": []}) == ([], [])
    assert M.parse_signal_payload({"data": [[{"values": []}]]}) == ([], [])
    # 缺 from → 無法推時間戳
    assert M.parse_signal_payload({"data": [[{"values": [1, 2]}]]}) == ([], [])


def test_fetch_signal_sends_unix_range_and_datasource():
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 7, 2, tzinfo=UTC)
    payload = {"data": [[{"from": int(start.timestamp()), "step": 3600,
                          "values": [1, 2, 3]}]]}
    session = _Session(_Resp(payload=payload))
    out = M.fetch_signal("4209", "ping-slash24", start, end, session)
    assert out["values"] == [1.0, 2.0, 3.0]
    url, params = session.calls[0]
    assert url.endswith("/signals/raw/region/4209")
    assert params["datasource"] == "ping-slash24"
    assert params["from"] == int(start.timestamp())
    assert params["until"] == int(end.timestamp())


def test_fetch_signal_returns_none_on_error():
    start, end = datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 2, tzinfo=UTC)
    assert M.fetch_signal("1", "bgp", start, end, _Session(_Resp(status=404))) is None
    assert M.fetch_signal("1", "bgp", start, end,
                          _Session(_Resp(payload={"data": []}))) is None


# ── 多來源互相印證 ──────────────────────────────────────────────────────────

def _series(ds, events):
    return {"datasource": ds, "anomalies": events}


def _ev(onset, end, severity="critical"):
    return {"onset": onset, "end": end, "severity": severity}


def test_two_sources_overlapping_counts_as_corroborated():
    s = [_series("bgp", [_ev("2026-07-10T02:00:00Z", "2026-07-10T05:00:00Z")]),
         _series("ping-slash24", [_ev("2026-07-10T03:00:00Z", "2026-07-10T06:00:00Z")])]
    M.annotate_corroboration(s)
    assert s[0]["anomalies"][0]["corroborating_sources"] == 2
    assert s[1]["anomalies"][0]["corroborating_sources"] == 2
    # 有互相印證就維持 critical
    assert s[0]["anomalies"][0]["severity"] == "critical"


def test_single_source_critical_is_downgraded():
    """只有一種訊號動，多半是該來源自己的量測問題，不該報 critical。"""
    s = [_series("bgp", [_ev("2026-07-10T02:00:00Z", "2026-07-10T05:00:00Z")]),
         _series("merit-nt", [_ev("2026-07-20T02:00:00Z", "2026-07-20T05:00:00Z")])]
    M.annotate_corroboration(s)
    for series in s:
        e = series["anomalies"][0]
        assert e["corroborating_sources"] == 1
        assert e["severity"] == "high"
        assert e["severity_downgraded"] == "single_source"


def test_three_sources_all_corroborate():
    win = ("2026-07-10T02:00:00Z", "2026-07-10T05:00:00Z")
    s = [_series(ds, [_ev(*win)]) for ds in ("bgp", "ping-slash24", "merit-nt")]
    M.annotate_corroboration(s)
    assert all(x["anomalies"][0]["corroborating_sources"] == 3 for x in s)


def test_non_overlapping_windows_are_not_corroborated():
    s = [_series("bgp", [_ev("2026-07-10T02:00:00Z", "2026-07-10T03:00:00Z", "high")]),
         _series("ping-slash24", [_ev("2026-07-15T02:00:00Z", "2026-07-15T03:00:00Z", "high")])]
    M.annotate_corroboration(s)
    assert s[0]["anomalies"][0]["corroborating_sources"] == 1


# ── summary ─────────────────────────────────────────────────────────────────

def test_summary_counts_by_island_and_corroboration():
    win = ("2026-07-10T02:00:00Z", "2026-07-10T05:00:00Z")
    matsu_series = [_series(ds, [_ev(*win)]) for ds in ("bgp", "ping-slash24")]
    M.annotate_corroboration(matsu_series)
    islands = [
        {"id": "lienchiang", "series": matsu_series},
        {"id": "kinmen", "series": [_series("bgp", [])]},
    ]
    s = M.build_summary(islands)
    assert s["islands_monitored"] == 2
    assert s["anomaly_count"] == 2
    assert s["by_island"] == {"lienchiang": 2}
    assert s["multi_source_corroborated"] == 2
    assert s["latest_anomaly_onset"] == win[0]


# ── 與 Cloudflare Radar 共用同一套判讀 ──────────────────────────────────────

def test_island_signal_uses_the_same_detection_as_radar():
    """馬祖有微波備援，斷纜不會歸零而是嚴重降速 —— 用相對基線的偵測才抓得到。"""
    import math
    start = datetime(2026, 7, 1, tzinfo=UTC)
    ts, vals = [], []
    for h in range(28 * 24):
        d = start + timedelta(hours=h)
        clean = 100 + 12 * math.sin((d.hour - 3) / 24 * 2 * math.pi)
        vals.append(clean)
        ts.append(d.isoformat().replace("+00:00", "Z"))
    for i in range(20 * 24, 20 * 24 + 6):      # 掉 55%，不是歸零
        vals[i] *= 0.45
    out = analyze_values(ts, vals, direction="drop")
    assert len(out["anomalies"]) == 1
    assert out["anomalies"][0]["severity"] == "critical"


def test_islands_config_covers_the_three_target_counties():
    ids = {i["id"] for i in M.ISLANDS}
    assert ids == {"kinmen", "lienchiang", "penghu"}
    for i in M.ISLANDS:
        assert 21 < i["lat"] < 27 and 117 < i["lon"] < 122


def test_datasources_are_the_three_independent_signals():
    assert [d["id"] for d in M.DATASOURCES] == ["bgp", "ping-slash24", "merit-nt"]


# ── 逐時重採樣與低計數守衛（實測 IODA 資料暴露出來的問題）─────────────────

from anomaly_detect import detect_anomalies, resample_hourly  # noqa: E402


def test_resample_hourly_collapses_five_minute_data():
    """IODA 的 bgp/merit-nt 是 5 分鐘、ping-slash24 是 10 分鐘，不是逐時。"""
    start = datetime(2026, 7, 1, tzinfo=UTC)
    ts = [(start + timedelta(minutes=5 * i)).isoformat().replace("+00:00", "Z")
          for i in range(24)]                      # 2 小時 × 12 點
    vals = [10.0] * 12 + [20.0] * 12
    out_ts, out_vals = resample_hourly(ts, vals)
    assert len(out_ts) == 2
    assert out_ts[0] == "2026-07-01T00:00:00Z"
    assert out_vals == [10.0, 20.0]


def test_resample_takes_median_not_mean():
    """低計數訊號偶爾的尖刺不該把整小時拉走。"""
    start = datetime(2026, 7, 1, tzinfo=UTC)
    ts = [(start + timedelta(minutes=5 * i)).isoformat().replace("+00:00", "Z")
          for i in range(12)]
    vals = [10.0] * 11 + [1000.0]                  # 一個尖刺
    _, out = resample_hourly(ts, vals)
    assert out == [10.0]


def test_resample_hour_with_only_gaps_becomes_none():
    start = datetime(2026, 7, 1, tzinfo=UTC)
    ts = [(start + timedelta(minutes=5 * i)).isoformat().replace("+00:00", "Z")
          for i in range(12)]
    _, out = resample_hourly(ts, [None] * 12)
    assert out == [None]


def test_resample_fixes_duration_semantics():
    """重採樣前『4 點』被當成 4 小時，實際只有 20 分鐘 —— 嚴重度差 12 倍。"""
    import math
    start = datetime(2026, 7, 1, tzinfo=UTC)
    ts, vals = [], []
    for i in range(28 * 24 * 12):                  # 28 天的 5 分鐘資料
        d = start + timedelta(minutes=5 * i)
        v = 100 + 20 * math.sin((d.hour - 3) / 24 * 2 * math.pi)
        vals.append(v)
        ts.append(d.isoformat().replace("+00:00", "Z"))
    out = analyze_values(ts, vals, direction="drop", resample=True)
    assert out["points"] == 28 * 24               # 已收斂成逐時
    assert out["anomalies"] == []


def test_low_count_guard_suppresses_percentage_noise():
    """澎湖的 darknet 值域在個位數，2→1 就是 -50%，一週噴出 12 件假異常。

    重點是**連續**幾個時段都掉到 1 —— 孤立的單點本來就會被 min_consecutive
    擋掉，真正漏過去的是低計數下的連續小波動。
    """
    start = datetime(2026, 7, 1, tzinfo=UTC)
    ts, vals = [], []
    for h in range(28 * 24):
        d = start + timedelta(hours=h)
        ts.append(d.isoformat().replace("+00:00", "Z"))
        # 每 24 小時有連續 3 小時掉到 1（背景輻射流量本來就是這樣跳動）
        vals.append(1.0 if (h % 24) in (3, 4, 5) else 2.0)
    baseline = [2.0] * len(vals)
    noisy = detect_anomalies(ts, vals, baseline=baseline, min_baseline_level=0)
    guarded = detect_anomalies(ts, vals, baseline=baseline, min_baseline_level=20)
    assert noisy, "沒有守衛時應該噴出假異常（重現實測狀況）"
    assert all(e["max_deviation_pct"] == -50.0 for e in noisy)
    assert guarded == [], "有守衛時應該全部濾掉"


def test_low_count_guard_does_not_suppress_real_signal_at_scale():
    """BGP 前綴數在 400~500，量級足夠，守衛不該擋掉真事件。"""
    import math
    start = datetime(2026, 7, 1, tzinfo=UTC)
    ts, vals = [], []
    for h in range(28 * 24):
        d = start + timedelta(hours=h)
        ts.append(d.isoformat().replace("+00:00", "Z"))
        vals.append(450 + 20 * math.sin((d.hour - 3) / 24 * 2 * math.pi))
    for i in range(20 * 24, 20 * 24 + 5):
        vals[i] *= 0.4                             # 掉 60%
    out = analyze_values(ts, vals, direction="drop", min_baseline_level=20)
    assert len(out["anomalies"]) == 1
    assert out["anomalies"][0]["severity"] == "critical"


def test_datasources_carry_min_level_floors():
    floors = {d["id"]: d.get("min_level") for d in M.DATASOURCES}
    assert floors["merit-nt"] >= 20      # 個位數值域，必須擋
    assert floors["bgp"] >= 10
    assert all(v is not None for v in floors.values())
