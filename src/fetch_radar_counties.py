#!/usr/bin/env python3
"""
Cloudflare Radar 縣市（ADM1）級網路指標 — Taiwan Gray Zone Monitor

`fetch_cloudflare_radar.py` 抓的是**國家級**（`location=TW`）序列。2025-09-29
Cloudflare 推出 Regional Data 之後，HTTP／NetFlows 的 summary 與
timeseries_groups 多了 `adm1` 維度與 **`geoId` 篩選**（GeoNames ID），
`/radar/geolocations` 可列出 ADM1 —— 台灣的 ADM1 就是 22 個縣市。
這支腳本就是把那個粒度接進來，讓前端能以縣市為區塊上色。

**指標階梯**（`METRIC_LADDER`）：Cloudflare 沒有明說「速度」類端點
（IQI 頻寬／延遲、Speed Test）吃不吃 `geoId`，所以程式**逐縣市依序試**，
第一個回得出序列的就採用，並把 `metric_id` 寫進輸出——前端據此標明這一格
到底是「實測頻寬」還是「流量指數」，不會把兩件事混為一談。
能力矩陣由 `src/probe_radar_regions.py`（手動觸發的 workflow）實測。

判讀邏輯完全沿用 `anomaly_detect`（與國家級、IODA 同一套），此處不重做。

用法:
  python3 src/fetch_radar_counties.py               # 抓 28 天、偵測、寫檔
  python3 src/fetch_radar_counties.py --days 14
  python3 src/fetch_radar_counties.py --refresh-geoids   # 強制重解析 geoId

輸出: data/cf_radar_counties.json
"""
import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from anomaly_detect import _parse_ts, analyze_values  # noqa: E402
from build_tw_counties import COUNTY_NAMES_EN, COUNTY_NAMES_ZH  # noqa: E402
from fetch_cloudflare_radar import (  # noqa: E402
    CF_API_BASE, REQUEST_TIMEOUT, TOKEN_ENV_NAMES, _get_env,
    parse_timeseries_payload,
)
from io_utils import atomic_write_json, load_json  # noqa: E402

DEFAULT_DAYS = 28
AGG_INTERVAL = "1h"

# 輸出序列的降採樣：這個檔每 2 小時 commit 一次，22 縣市 × 逐時 × 14 天原封不動
# 寫出去，repo 會被撐爆（本專案有過 200MB+ 的前科，見 CLAUDE.md）。
# 3 小時一格 × 7 天 ≈ 56 點／縣市，畫縮圖夠用，偵測仍吃完整逐時序列。
OUTPUT_BUCKET_HOURS = 3
OUTPUT_RETAIN_DAYS = 7

# 「目前異常」的認定：最後一筆異常結束於這麼多小時內
ONGOING_WINDOW_HOURS = 6

# geoId 快取（GeoNames ID 幾乎不會變，但也不寫死——實測前無從驗證）
GEOID_CACHE = DATA_DIR / "radar_geolocations_tw.json"
GEOID_CACHE_DAYS = 30

# 逐縣市依序嘗試，第一個成功的就是這個縣市的指標。
# `direction` 給 anomaly_detect：drop＝越低越糟，spike＝越高越糟。
METRIC_LADDER = [
    {
        "id": "iqi_bandwidth",
        "label_zh": "頻寬（IQI 中位數）", "label_en": "Bandwidth (IQI median)",
        "unit": "Mbps", "direction": "drop", "higher_is_better": True,
        "is_speed": True,
        "path": "radar/quality/iqi/timeseries_groups",
        "params": {"metric": "bandwidth"},
    },
    {
        "id": "iqi_latency",
        "label_zh": "延遲（IQI 中位數）", "label_en": "Latency (IQI median)",
        "unit": "ms", "direction": "spike", "higher_is_better": False,
        "is_speed": True,
        "path": "radar/quality/iqi/timeseries_groups",
        "params": {"metric": "latency"},
    },
    {
        # 沒有速度資料時的退路：流量指數不是網速，是「這個縣市送出多少流量」，
        # 海纜或幹線出事一樣會掉，但不能拿來說「網速多少 Mbps」。
        "id": "netflows_traffic",
        "label_zh": "流量指數（NetFlows）", "label_en": "Traffic index (NetFlows)",
        "unit": "index", "direction": "drop", "higher_is_better": True,
        "is_speed": False,
        "path": "radar/netflows/timeseries",
        "params": {},
    },
]


def county_roster():
    """22 縣市的靜態名冊（ISO ↔ 中英文名），與縣市界圖共用同一份對照表。"""
    return [{"iso": iso, "name_zh": COUNTY_NAMES_ZH[iso],
             "name_en": COUNTY_NAMES_EN[iso]}
            for iso in sorted(COUNTY_NAMES_ZH)]


# ── geoId 解析 ──────────────────────────────────────────────────────────────

def _normalize_name(name):
    """比對用的名稱正規化：小寫、去掉 County/City 這種行政後綴與標點。"""
    text = (name or "").lower()
    for token in (" county", " city", " prefecture", " islands", " island"):
        text = text.replace(token, " ")
    return "".join(ch for ch in text if ch.isalnum())


NAME_ALIASES = {
    # Radar 端可能用的別名 → 我們的 ISO 代碼
    "matsu": "TW-LIE", "lienkiang": "TW-LIE", "lienchiang": "TW-LIE",
    "newtaipei": "TW-NWT", "taipeicounty": "TW-NWT",
    "keelung": "TW-KEE", "chiayi": "TW-CYQ", "hsinchu": "TW-HSQ",
}


def match_geoids(entries, roster=None):
    """把 Radar 回來的 ADM1 entity 對到我們的 ISO 代碼。

    先用 ISO（最可靠），沒有 ISO 才用正規化後的英文名；兩者都對不上就丟掉——
    寧可少一個縣市顯示「資料不足」，也不要把宜蘭的數字畫到花蓮頭上。
    """
    roster = roster or county_roster()
    by_iso = {c["iso"]: c for c in roster}
    by_name = {}
    for c in roster:
        by_name.setdefault(_normalize_name(c["name_en"]), c["iso"])
    by_name.update({k: v for k, v in NAME_ALIASES.items()})

    out = {}
    for entry in entries or []:
        geo_id = entry.get("geo_id") or entry.get("geoId") or entry.get("id")
        if geo_id is None:
            continue
        iso = (entry.get("iso") or "").upper()
        if iso not in by_iso:
            iso = by_name.get(_normalize_name(entry.get("name")), "")
        if iso in by_iso and iso not in out:
            out[iso] = str(geo_id)
    return out


def _radar_get(session, token, path, params):
    """打一個 Radar 端點，回 (payload|None, status)。失敗不丟例外。"""
    try:
        resp = session.get(f"{CF_API_BASE}/{path}",
                           params={"format": "json", **params},
                           headers={"Authorization": f"Bearer {token}"},
                           timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        print(f"⚠️ [{path}] 請求失敗: {e}")
        return None, None
    if resp.status_code != 200:
        return None, resp.status_code
    try:
        payload = resp.json()
    except ValueError:
        return None, resp.status_code
    if not payload.get("success", True):
        return None, resp.status_code
    return payload, resp.status_code


def extract_adm1_entities(payload):
    """從回應遞迴撈出 ADM1 entity（geo_id / name / iso）。

    Radar 各端點的殼不一致（`result.geolocations`、`result.adm1`…），官方文件
    也沒寫全，因此不硬綁 key：任何同時帶 id 與 name 的 dict 都收下，正確性由
    後續的 ISO／名稱比對把關。
    """
    found = {}

    def walk(node):
        if isinstance(node, dict):
            geo_id = (node.get("geoId") or node.get("geo_id")
                      or node.get("id") or node.get("code"))
            name = node.get("name") or node.get("locationName") or node.get("label")
            iso = (node.get("iso3166Alpha2") or node.get("isoCode")
                   or node.get("subdivisionCode") or node.get("alpha2"))
            if geo_id is not None and name:
                found[str(geo_id)] = {"geo_id": str(geo_id), "name": str(name),
                                      "iso": (str(iso) if iso else None)}
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return list(found.values())


GEOID_LOOKUPS = [
    ("radar/geolocations", {"location": "TW", "limit": 100}),
    ("radar/geolocations", {"location": "TW", "type": "ADM1", "limit": 100}),
    ("radar/geolocations", {"countryAlpha2": "TW", "limit": 100}),
    ("radar/netflows/summary_v2", {"dimension": "adm1", "location": "TW",
                                   "dateRange": "7d"}),
]


def resolve_county_geoids(session, token, cache_path=GEOID_CACHE, refresh=False):
    """ISO → geoId。優先讀快取（30 天），過期或 --refresh-geoids 才重打 API。"""
    cache = load_json(cache_path, {}, label="radar geoId 快取", expect_type=dict)
    if not refresh and cache.get("geoids"):
        resolved_at = _parse_ts(cache.get("resolved_at"))
        fresh = resolved_at and (datetime.now(timezone.utc) - resolved_at
                                 < timedelta(days=GEOID_CACHE_DAYS))
        if fresh:
            print(f"🗂️  geoId 快取命中（{len(cache['geoids'])} 縣市）")
            return cache["geoids"]

    for path, params in GEOID_LOOKUPS:
        payload, status = _radar_get(session, token, path, params)
        if not payload:
            print(f"   ↳ [{path}] HTTP {status}")
            continue
        geoids = match_geoids(extract_adm1_entities(payload))
        if geoids:
            print(f"   ✅ [{path}] 解析出 {len(geoids)} 個縣市 geoId")
            atomic_write_json(cache_path, {
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "source": path,
                "geoids": geoids,
            })
            return geoids
        print(f"   ↳ [{path}] 回應裡沒有可辨識的台灣 ADM1")

    if cache.get("geoids"):
        print("⚠️ 重新解析失敗，沿用過期的 geoId 快取")
        return cache["geoids"]
    return {}


# ── 指標抓取 ────────────────────────────────────────────────────────────────

def fetch_metric_series(session, token, geo_id, metric, days=DEFAULT_DAYS):
    """抓某縣市的某個指標序列。拿不到（端點不吃 geoId／樣本不足）回 None。"""
    payload, status = _radar_get(session, token, metric["path"], {
        "dateRange": f"{days}d",
        "aggInterval": AGG_INTERVAL,
        "geoId": geo_id,
        **metric.get("params", {}),
    })
    if not payload:
        return None, status
    timestamps, values = parse_timeseries_payload(payload)
    if not timestamps or not any(v is not None for v in values):
        return None, status
    return {"timestamps": timestamps, "values": values}, status


def fetch_county_metric(session, token, geo_id, days=DEFAULT_DAYS,
                        ladder=None):
    """依階梯逐一嘗試，回 (metric, series, attempts)；全部失敗 metric 為 None。"""
    attempts = []
    for metric in (ladder or METRIC_LADDER):
        series, status = fetch_metric_series(session, token, geo_id, metric, days)
        attempts.append({"metric_id": metric["id"], "status": status,
                         "ok": series is not None})
        if series is not None:
            return metric, series, attempts
    return None, None, attempts


# ── 輸出組裝（純函式）──────────────────────────────────────────────────────

def downsample_series(timestamps, values, bucket_hours=OUTPUT_BUCKET_HOURS,
                      retain_days=OUTPUT_RETAIN_DAYS):
    """把逐時序列壓成 bucket_hours 一格的中位數（只留最近 retain_days）。

    偵測吃的是完整逐時序列；這裡壓的只是寫進 JSON 給前端畫縮圖的那份。
    取中位數而非平均：一個量測缺口造成的 0 不該把整格拉低。
    """
    keep = int(retain_days * 24)
    timestamps = list(timestamps)[-keep:]
    values = list(values)[-keep:]
    out_ts, out_vals = [], []
    for i in range(0, len(timestamps), bucket_hours):
        chunk = [v for v in values[i:i + bucket_hours] if v is not None]
        out_ts.append(timestamps[i])
        out_vals.append(round(statistics.median(chunk), 2) if chunk else None)
    return out_ts, out_vals


def latest_reading(analysis):
    """最後一筆有值的觀測與其基線 → (value, baseline, pct_vs_baseline)。"""
    values = analysis.get("values") or []
    baseline = analysis.get("baseline") or []
    for i in range(len(values) - 1, -1, -1):
        if values[i] is None:
            continue
        base = baseline[i] if i < len(baseline) else None
        pct = None
        if base:
            pct = round((values[i] - base) / base * 100, 1)
        return values[i], base, pct
    return None, None, None


def classify_level(anomalies, now=None):
    """異常事件 → 地圖色階。無事件＝normal，近期有＝watch，仍在進行＝alert。"""
    if not anomalies:
        return "normal"
    now = now or datetime.now(timezone.utc)
    for event in anomalies:
        end = _parse_ts(event.get("end") or event.get("onset"))
        if end and (now - end) <= timedelta(hours=ONGOING_WINDOW_HOURS):
            return "alert"
    return "watch"


def build_county_record(county, metric, analysis, geo_id=None, now=None):
    """組一筆縣市輸出（純函式，方便單測）。"""
    value, baseline, pct = latest_reading(analysis)
    timestamps, values = downsample_series(analysis.get("timestamps") or [],
                                           analysis.get("values") or [])
    anomalies = analysis.get("anomalies") or []
    return {
        "iso": county["iso"],
        "name_zh": county["name_zh"],
        "name_en": county["name_en"],
        "geo_id": geo_id,
        "status": "available",
        "metric_id": metric["id"],
        "metric_label_zh": metric["label_zh"],
        "metric_label_en": metric["label_en"],
        "unit": metric["unit"],
        "higher_is_better": metric["higher_is_better"],
        "is_speed": metric["is_speed"],
        "latest": value,
        "baseline": baseline,
        "pct_vs_baseline": pct,
        "level": classify_level(anomalies, now=now),
        "points": analysis.get("points"),
        "baseline_coverage": analysis.get("baseline_coverage"),
        "anomalies": anomalies,
        "series": {"timestamps": timestamps, "values": values,
                   "bucket_hours": OUTPUT_BUCKET_HOURS},
    }


def unavailable_record(county, reason, geo_id=None):
    """沒有資料的縣市也要寫進輸出——地圖上是灰色，不是「正常」。"""
    return {
        "iso": county["iso"], "name_zh": county["name_zh"],
        "name_en": county["name_en"], "geo_id": geo_id,
        "status": "unavailable", "error_reason": reason,
        "metric_id": None, "level": "unknown", "latest": None,
        "anomalies": [], "series": {"timestamps": [], "values": [],
                                     "bucket_hours": OUTPUT_BUCKET_HOURS},
    }


def build_summary(counties):
    availability, levels = {}, {}
    anomaly_count = 0
    for c in counties:
        levels[c["level"]] = levels.get(c["level"], 0) + 1
        anomaly_count += len(c.get("anomalies") or [])
        if c.get("metric_id"):
            availability[c["metric_id"]] = availability.get(c["metric_id"], 0) + 1
    available = [c for c in counties if c["status"] == "available"]
    speed_counties = [c for c in available if c.get("is_speed")]
    return {
        "counties_total": len(counties),
        "counties_with_data": len(available),
        "counties_with_speed_metric": len(speed_counties),
        "metric_availability": availability,
        "by_level": levels,
        "anomaly_count": anomaly_count,
        "latest_anomaly_onset": max(
            (e["onset"] for c in counties for e in (c.get("anomalies") or [])),
            default=None),
    }


def main():
    ap = argparse.ArgumentParser(description="Cloudflare Radar 縣市級網路指標")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--refresh-geoids", action="store_true",
                    help="忽略快取，重新向 Radar 解析縣市 geoId")
    ap.add_argument("-o", "--output",
                    default=str(DATA_DIR / "cf_radar_counties.json"))
    args = ap.parse_args()

    token = _get_env(*TOKEN_ENV_NAMES)
    if not token:
        print("❌ 缺少 Cloudflare API token（CLOUDFLARE_API_TOKEN / "
              "CLAUDEFARETOKEN），略過")
        sys.exit(1)

    session = requests.Session()
    print("🌍 解析縣市 geoId …")
    geoids = resolve_county_geoids(session, token, refresh=args.refresh_geoids)
    if not geoids:
        print("❌ Radar 沒有回傳任何台灣 ADM1 geoId —— 這個帳號／版本可能還沒有"
              "區域資料，先跑 src/probe_radar_regions.py 看能力矩陣")
        sys.exit(1)

    counties = []
    for county in county_roster():
        geo_id = geoids.get(county["iso"])
        if not geo_id:
            print(f"⚠️ {county['name_zh']}：Radar 沒有對應的 geoId")
            counties.append(unavailable_record(county, "geoid_not_found"))
            continue
        metric, series, attempts = fetch_county_metric(session, token, geo_id,
                                                       days=args.days)
        if metric is None:
            tried = ",".join(f"{a['metric_id']}:{a['status']}" for a in attempts)
            print(f"⚠️ {county['name_zh']}：所有指標都拿不到（{tried}）")
            counties.append(unavailable_record(county, "no_metric_available",
                                               geo_id))
            continue
        analysis = analyze_values(series["timestamps"], series["values"],
                                  direction=metric["direction"])
        record = build_county_record(county, metric, analysis, geo_id=geo_id)
        counties.append(record)
        print(f"✅ {county['name_zh']}：{metric['label_zh']} "
              f"{record['latest']}{metric['unit']}｜{analysis['points']} 點｜"
              f"異常 {len(analysis['anomalies'])} 件")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": args.days,
        "agg_interval": AGG_INTERVAL,
        "source": "Cloudflare Radar (ADM1 / geoId)",
        "metric_ladder": [{k: m[k] for k in
                           ("id", "label_zh", "label_en", "unit",
                            "higher_is_better", "is_speed")}
                          for m in METRIC_LADDER],
        "counties": counties,
        "summary": build_summary(counties),
    }
    out = Path(args.output)
    atomic_write_json(out, payload, compact=True)
    print(f"✅ {out}  {json.dumps(payload['summary'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
