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
