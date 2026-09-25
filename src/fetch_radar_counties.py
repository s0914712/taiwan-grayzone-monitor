#!/usr/bin/env python3
"""
Cloudflare Radar 分區（ADM1）級網路指標 — Taiwan Gray Zone Monitor

`fetch_cloudflare_radar.py` 抓的是**國家級**（`location=TW`）序列。2025-09-29
Cloudflare 推出 Regional Data 之後，多了 **`geoId` 篩選**（GeoNames ID），
可以拿到第一級行政區（ADM1）的資料。

⚠️ **Radar 的台灣 ADM1 只有 4 個分區，不是 22 個縣市**（實測：
`probe_radar_regions.py`，Actions run 33239842250）。用的是 GeoNames 的舊台灣
省制分區，entity 也**沒有 ISO 3166-2 欄位**（`code` 是 GeoNames 分區碼）：

    7280290 Taipei  → 臺北市
    7280289 Takao   → 高雄市（打狗）
    7280288 Fukien  → **金門縣 + 連江縣（馬祖）**
    7280291 Taiwan  → 其餘 18 個縣市

所以「每個縣市各自的網速」在 Radar 上**不存在**。這支腳本抓的是這 4 個分區，
輸出時再展開成 22 筆縣市記錄（`is_group_value: True`），讓前端能以縣市界上色、
同時在每一格標明「這是分區值，不是本縣市單獨量測」——把分區值講成縣市值就是
謊報。真正逐縣市的訊號只有 `fetch_ioda.py` 那條（可達性）。

**判讀價值**：Fukien 分區＝金門＋馬祖，是 Radar 唯一單獨切出來的離島分區。
馬祖正是 IODA 完全沒有資料的那一座島（見 `src/CLAUDE.md` 的否定結果），
這條線因此是馬祖唯一的頻寬／延遲量測來源。

**指標階梯**（`METRIC_LADDER`）：逐分區依序試 IQI 頻寬 → IQI 延遲 → NetFlows
流量指數，第一個回得出序列的就採用，`metric_id`／`is_speed` 寫進輸出——前端
據此標明這一格是「實測頻寬」還是「流量指數」，不會把兩件事混為一談。
能力矩陣由 `src/probe_radar_regions.py`（手動觸發的 workflow）實測。

判讀邏輯完全沿用 `anomaly_detect`（與國家級、IODA 同一套），此處不重做。

用法:
  python3 src/fetch_radar_counties.py               # 抓 28 天、偵測、寫檔
  python3 src/fetch_radar_counties.py --days 14
  python3 src/fetch_radar_counties.py --refresh-geoids   # 強制重解析分區 geoId

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

# Speed Test 摘要的取樣窗。實測資料稀疏（縣市級的測速樣本本來就少），
# 視窗開大一點才不會整片空白；這個數字不參與異常偵測，只是顯示用的實測中位數。
SPEED_TEST_DAYS = 28

# geoId 快取（GeoNames ID 幾乎不會變，但也不寫死——實測前無從驗證）
GEOID_CACHE = DATA_DIR / "radar_geolocations_tw.json"
GEOID_CACHE_DAYS = 30

# 逐縣市依序嘗試，第一個成功的就是這個縣市的指標。
# `direction` 給 anomaly_detect：drop＝越低越糟，spike＝越高越糟。
#
# **實測結論**（`probe_radar_regions.py`，Actions run 33216931232，台北 geoId
# 7280290）：速度類端點**全部吃 `geoId`** —— IQI 頻寬 200（14.78 Mbps）、IQI 延遲
# 200（71.49 ms）、IQI summary 200、Speed Test summary 200、speed histogram 200、
# HTTP timeseries 200、NetFlows timeseries 200。所以第一級就會命中，縣市地圖的
# 「網速」模式是實測數值，`netflows_traffic` 只是理論上的退路。
# 對照組 `location=TW-LIE` 仍是 400（alpha-2 限制沒變，變的是多了 geoId 這條路）。
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


# ── Radar 的台灣 ADM1 分區 ──────────────────────────────────────────────────
# geo_id 是**實測驗證過**的（run 33239842250 印出完整 entity），不是猜的，因此
# 可以當靜態表用；程式仍會向 `/radar/geolocations` 動態解析並覆寫，Radar 哪天改
# 代碼也不會整支壞掉。`members` 是這個分區涵蓋的 ISO 3166-2 縣市。
_SPECIAL_MEMBERS = {"TW-TPE", "TW-KHH", "TW-KIN", "TW-LIE"}

RADAR_ADM1_GROUPS = [
    {"id": "taipei", "geo_id": "7280290", "radar_name": "Taipei",
     "label_zh": "臺北市", "label_en": "Taipei",
     "members": ["TW-TPE"]},
    {"id": "takao", "geo_id": "7280289", "radar_name": "Takao",
     "label_zh": "高雄市", "label_en": "Kaohsiung (Takao)",
     "members": ["TW-KHH"]},
    # Radar 唯一單獨切出來的離島分區。馬祖在 IODA 沒有資料，這是它唯一的頻寬來源。
    {"id": "fukien", "geo_id": "7280288", "radar_name": "Fukien",
     "label_zh": "福建省（金門・馬祖）", "label_en": "Fukien (Kinmen & Matsu)",
     "members": ["TW-KIN", "TW-LIE"]},
    {"id": "taiwan_province", "geo_id": "7280291", "radar_name": "Taiwan",
     "label_zh": "臺灣省（其餘 18 縣市）",
     "label_en": "Taiwan Province (other 18 counties)",
     "members": [iso for iso in sorted(COUNTY_NAMES_ZH)
                 if iso not in _SPECIAL_MEMBERS]},
]

# Radar 名稱 → 分區 id。名稱是 GeoNames 的舊分區名（Takao＝高雄舊名打狗、
# Fukien＝福建），因此別名要收得寬一點。
GROUP_NAME_ALIASES = {
    "taipei": "taipei",
    "takao": "takao", "kaohsiung": "takao",
    "fukien": "fukien", "fujian": "fukien", "kinmen": "fukien", "matsu": "fukien",
    "taiwan": "taiwan_province", "taiwanprovince": "taiwan_province",
}


def group_by_iso(groups=None):
    """ISO → 分區 spec，供展開縣市記錄時查表。"""
    out = {}
    for spec in (groups or RADAR_ADM1_GROUPS):
        for iso in spec["members"]:
            out[iso] = spec
    return out


# ── geoId 解析 ──────────────────────────────────────────────────────────────

def _normalize_name(name):
    """比對用的名稱正規化：小寫、去掉 County/City 這種行政後綴與標點。"""
    text = (name or "").lower()
    for token in (" county", " city", " prefecture", " islands", " island"):
        text = text.replace(token, " ")
    return "".join(ch for ch in text if ch.isalnum())


def match_group_geoids(entries, groups=None):
    """把 Radar 回來的 entity 對到分區 id → geoId。

    **必須是 `type == "ADM1"`**：同一份回應裡「Taiwan」出現兩次——一次是
    COUNTRY（geoId 1668284），一次是臺灣省 ADM1（7280291）。不看 type 就會把
    整個國家的數值當成臺灣省分區畫上去。
    entity 沒有 ISO 3166-2 欄位（實測），所以只能用名稱比對。
    """
    groups = groups or RADAR_ADM1_GROUPS
    known = {spec["id"] for spec in groups}
    out = {}
    for entry in entries or []:
        geo_id = entry.get("geo_id") or entry.get("geoId") or entry.get("id")
        if geo_id is None:
            continue
        entity_type = (entry.get("type") or "").upper()
        if entity_type and entity_type != "ADM1":
            continue
        group_id = GROUP_NAME_ALIASES.get(_normalize_name(entry.get("name")))
        if group_id in known and group_id not in out:
            out[group_id] = str(geo_id)
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
                                      "type": node.get("type"),
                                      "iso": (str(iso) if iso else None)}
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return list(found.values())


# 依實測調整過的清單。拿掉了 `radar/{http,netflows}/summary_v2` —— 那兩個路徑
# 根本不存在（實測回 400 `code 7000 No route for that URI`），正確的維度端點是
# `summary/{dimension}` 與 `timeseries_groups/{dimension}`。
# `countryAlpha2=TW` 也拿掉：實測它被忽略，回的是 183 筆全球清單。
GEOID_LOOKUPS = [
    ("radar/geolocations", {"location": "TW", "limit": 500}),
    ("radar/geolocations", {"location": "TW", "type": "ADM1", "limit": 500}),
    ("radar/http/summary/adm1", {"location": "TW", "dateRange": "7d"}),
    ("radar/http/timeseries_groups/adm1", {"location": "TW", "dateRange": "7d",
                                           "aggInterval": "1d"}),
]


def static_group_geoids(groups=None):
    """實測驗證過的分區 geoId（動態解析失敗時的保底）。"""
    return {spec["id"]: spec["geo_id"] for spec in (groups or RADAR_ADM1_GROUPS)}


def resolve_group_geoids(session, token, cache_path=GEOID_CACHE, refresh=False):
    """分區 id → geoId。快取 30 天；動態解析失敗時退回實測驗證過的靜態表。

    靜態表能當保底是因為那四個 geoId 是**實跑確認**的（不是猜的）；動態解析仍
    優先，Radar 若改了代碼才不會整支壞掉。
    """
    cache = load_json(cache_path, {}, label="radar 分區 geoId 快取",
                      expect_type=dict)
    if not refresh and cache.get("groups"):
        resolved_at = _parse_ts(cache.get("resolved_at"))
        fresh = resolved_at and (datetime.now(timezone.utc) - resolved_at
                                 < timedelta(days=GEOID_CACHE_DAYS))
        if fresh:
            print(f"🗂️  分區 geoId 快取命中（{len(cache['groups'])} 個分區）")
            return {**static_group_geoids(), **cache["groups"]}

    for path, params in GEOID_LOOKUPS:
        payload, status = _radar_get(session, token, path, params)
        if not payload:
            print(f"   ↳ [{path}] HTTP {status}")
            continue
        geoids = match_group_geoids(extract_adm1_entities(payload))
        if geoids:
            print(f"   ✅ [{path}] 解析出 {len(geoids)} 個分區 geoId：{geoids}")
            atomic_write_json(cache_path, {
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "source": path,
                "groups": geoids,
            })
            return {**static_group_geoids(), **geoids}
        print(f"   ↳ [{path}] 回應裡沒有可辨識的台灣 ADM1 分區")

    if cache.get("groups"):
        print("⚠️ 重新解析失敗，沿用過期的分區 geoId 快取")
        return {**static_group_geoids(), **cache["groups"]}
    print("⚠️ 動態解析失敗，改用實測驗證過的靜態分區表")
    return static_group_geoids()


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


# Speed Test 摘要要抓哪些欄位（實測回應是字串數值）。
# 這是**使用者實跑 speed.cloudflare.com 的中位數**，與 IQI 頻寬（Cloudflare 的
# 品質指數分位數）不是同一種量測：台北實測 IQI p50 = 14.3 Mbps，Speed Test
# 下載中位數 = 124.5 Mbps，差一個量級。兩個數字都要標明來源，混講就是謊報。
SPEED_TEST_FIELDS = {
    "bandwidthDownload": "bandwidth_download",
    "bandwidthUpload": "bandwidth_upload",
    "latencyIdle": "latency_idle",
    "latencyLoaded": "latency_loaded",
    "jitterIdle": "jitter_idle",
    "jitterLoaded": "jitter_loaded",
    "packetLoss": "packet_loss",
}


def parse_speed_summary(payload):
    """從 `quality/speed/summary` 回應撈出實測值；沒有可用欄位回 None。

    回應形狀（實測）：`{"result": {"summary_0": {"bandwidthDownload": "124.51",
    "bandwidthUpload": "65.25", ...}}}`。key 名稱（`summary_0`）不硬綁——
    找第一個帶 `bandwidthDownload` 的 dict。
    """
    result = (payload or {}).get("result") or {}
    if not isinstance(result, dict):
        return None
    for key, value in sorted(result.items()):
        if key == "meta" or not isinstance(value, dict):
            continue
        if "bandwidthDownload" not in value:
            continue
        out = {}
        for src, dest in SPEED_TEST_FIELDS.items():
            num = _to_float(value.get(src))
            if num is not None:
                out[dest] = round(num, 2)
        return out or None
    return None


def _to_float(value):
    """Radar 的數值以字串回傳（"124.512334"）；轉不動或 NaN 回 None。"""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return None if num != num else num


def fetch_speed_test(session, token, geo_id, days=SPEED_TEST_DAYS):
    """抓某分區的 Speed Test 實測摘要。拿不到回 None（不影響主指標）。"""
    payload, _ = _radar_get(session, token, "radar/quality/speed/summary",
                            {"dateRange": f"{days}d", "geoId": geo_id})
    return parse_speed_summary(payload)


def fetch_group_metric(session, token, geo_id, days=DEFAULT_DAYS,
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


def build_group_record(spec, metric, analysis, geo_id=None, now=None,
                       speed_test=None):
    """組一筆**分區**輸出（純函式，方便單測）。

    `speed_test` 是 Speed Test 實測摘要（`parse_speed_summary`），與色階用的
    `metric` 分開存：色階要的是有基線、能做異常偵測的時間序列，實測中位數只是
    給人看的補充數字。
    """
    value, baseline, pct = latest_reading(analysis)
    timestamps, values = downsample_series(analysis.get("timestamps") or [],
                                           analysis.get("values") or [])
    anomalies = analysis.get("anomalies") or []
    return {
        "group_id": spec["id"],
        "radar_name": spec["radar_name"],
        "label_zh": spec["label_zh"],
        "label_en": spec["label_en"],
        "members": list(spec["members"]),
        "geo_id": geo_id or spec["geo_id"],
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
        "speed_test": speed_test,
        "series": {"timestamps": timestamps, "values": values,
                   "bucket_hours": OUTPUT_BUCKET_HOURS},
    }


def unavailable_group_record(spec, reason, geo_id=None):
    """沒有資料的分區也要寫進輸出——地圖上是灰色，不是「正常」。"""
    return {
        "group_id": spec["id"], "radar_name": spec["radar_name"],
        "label_zh": spec["label_zh"], "label_en": spec["label_en"],
        "members": list(spec["members"]), "geo_id": geo_id or spec["geo_id"],
        "status": "unavailable", "error_reason": reason,
        "metric_id": None, "level": "unknown", "latest": None,
        "anomalies": [], "speed_test": None,
        "series": {"timestamps": [], "values": [],
                   "bucket_hours": OUTPUT_BUCKET_HOURS},
    }


# 展開成縣市時**不複製**的欄位：那是分區層的身分，複製過去會讓人以為
# 這一格是該縣市自己的量測。
_GROUP_ONLY_KEYS = {"group_id", "radar_name", "label_zh", "label_en", "members"}


def county_records_from_groups(groups, roster=None, differentiated=True):
    """分區記錄 → 22 筆縣市記錄（純函式）。

    前端的資料契約是「一個縣市一筆」，但 Radar 給的是 4 個分區，因此每筆都帶
    `is_group_value: True` 與所屬分區的標籤——地圖可以照縣市界上色，同時在每一格
    誠實標明「這是分區值，不是本縣市單獨量測」。

    `differentiated=False`（`detect_geoid_ignored` 判定 Radar 忽略了 geoId）時，
    每筆改標 `status="national_only"`：前端據此**不以此上色**，只在圖例顯示一個
    全國值。把全國值畫成四塊分區，比沒有資料更糟。
    """
    roster = roster or county_roster()
    by_iso = {}
    for group in groups:
        for iso in group.get("members") or []:
            by_iso[iso] = group

    out = []
    for county in roster:
        group = by_iso.get(county["iso"])
        if group is None:
            out.append({
                "iso": county["iso"], "name_zh": county["name_zh"],
                "name_en": county["name_en"], "status": "unavailable",
                "error_reason": "no_adm1_group", "metric_id": None,
                "level": "unknown", "latest": None, "anomalies": [],
                "speed_test": None, "is_group_value": False,
                "series": {"timestamps": [], "values": [],
                           "bucket_hours": OUTPUT_BUCKET_HOURS},
            })
            continue
        record = {k: v for k, v in group.items() if k not in _GROUP_ONLY_KEYS}
        record.update({
            "iso": county["iso"],
            "name_zh": county["name_zh"],
            "name_en": county["name_en"],
            "is_group_value": bool(differentiated),
            "adm1_group_id": group["group_id"],
            "adm1_group_label_zh": group["label_zh"],
            "adm1_group_label_en": group["label_en"],
            "adm1_group_members": list(group.get("members") or []),
        })
        if not differentiated and record.get("status") == "available":
            record["status"] = "national_only"
            record["error_reason"] = "geoid_ignored_by_radar"
        out.append(record)
    return out


def detect_geoid_ignored(groups):
    """四個分區的值完全相同 → Radar 靜默忽略了 `geoId`。

    **這是實跑管線才抓到的假陽性**（2026-08-29）：探測只驗「HTTP 200 且有值」，
    但 quality 端點對 `geoId` 是照收不誤、照回全國值。四個地理位置差極遠的分區
    （臺北／高雄／金馬／臺灣省）不可能連續 28 天逐點相同，連 Speed Test 的每個
    欄位都一樣更不可能——那只會是同一份全國資料被回了四次。

    偵測到就不能把這些數字當分區值用：寧可只顯示一個全國值，也不要在地圖上畫出
    四塊「各自量到的網速」。
    """
    available = [g for g in groups if g.get("status") == "available"]
    if len(available) < 2:
        return False

    def signature(group):
        return (json.dumps(group.get("series", {}).get("values")),
                json.dumps(group.get("speed_test"), sort_keys=True))

    first = signature(available[0])
    return all(signature(g) == first for g in available[1:])


def build_summary(groups, counties):
    """摘要以**分區**為主體計數。

    「22 個縣市裡有幾個拿到網速」是誤導性的數字——資料只有 4 個分區，
    縣市數只是分區覆蓋範圍的副產品，所以縣市那邊只計「被分區覆蓋幾個」。
    """
    availability, levels = {}, {}
    anomaly_count = 0
    for g in groups:
        levels[g["level"]] = levels.get(g["level"], 0) + 1
        anomaly_count += len(g.get("anomalies") or [])
        if g.get("metric_id"):
            availability[g["metric_id"]] = availability.get(g["metric_id"], 0) + 1
    available = [g for g in groups if g["status"] == "available"]
    return {
        "geoid_differentiated": all(g.get("differentiated") is not False
                                    for g in groups),
        "adm1_groups_total": len(groups),
        "adm1_groups_with_data": len(available),
        "adm1_groups_with_speed_metric": sum(1 for g in available
                                             if g.get("is_speed")),
        "adm1_groups_with_speed_test": sum(
            1 for g in groups
            if (g.get("speed_test") or {}).get("bandwidth_download") is not None),
        "counties_total": len(counties),
        "counties_covered_by_group": sum(1 for c in counties
                                         if c.get("is_group_value")),
        "metric_availability": availability,
        "by_level": levels,
        "anomaly_count": anomaly_count,
        "latest_anomaly_onset": max(
            (e["onset"] for g in groups for e in (g.get("anomalies") or [])),
            default=None),
    }


def main():
    ap = argparse.ArgumentParser(description="Cloudflare Radar 分區級網路指標")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--refresh-geoids", action="store_true",
                    help="忽略快取，重新向 Radar 解析分區 geoId")
    ap.add_argument("-o", "--output",
                    default=str(DATA_DIR / "cf_radar_counties.json"))
    args = ap.parse_args()

    token = _get_env(*TOKEN_ENV_NAMES)
    if not token:
        print("❌ 缺少 Cloudflare API token（CLOUDFLARE_API_TOKEN / "
              "CLAUDEFARETOKEN），略過")
        sys.exit(1)

    session = requests.Session()
    print("🌍 解析分區 geoId …")
    geoids = resolve_group_geoids(session, token, refresh=args.refresh_geoids)

    groups = []
    for spec in RADAR_ADM1_GROUPS:
        geo_id = geoids.get(spec["id"], spec["geo_id"])
        metric, series, attempts = fetch_group_metric(session, token, geo_id,
                                                      days=args.days)
        if metric is None:
            tried = ",".join(f"{a['metric_id']}:{a['status']}" for a in attempts)
            print(f"⚠️ {spec['label_zh']}：所有指標都拿不到（{tried}）")
            groups.append(unavailable_group_record(spec, "no_metric_available",
                                                   geo_id))
            continue
        analysis = analyze_values(series["timestamps"], series["values"],
                                  direction=metric["direction"])
        speed_test = fetch_speed_test(session, token, geo_id)
        record = build_group_record(spec, metric, analysis, geo_id=geo_id,
                                    speed_test=speed_test)
        groups.append(record)
        speed_note = ""
        if speed_test and speed_test.get("bandwidth_download") is not None:
            speed_note = (f"｜Speed Test 下載 "
                          f"{speed_test['bandwidth_download']} Mbps")
        print(f"✅ {record['label_zh']}（{spec['radar_name']} / {geo_id}）："
              f"{metric['label_zh']} {record['latest']}{metric['unit']}｜"
              f"{analysis['points']} 點｜異常 {len(analysis['anomalies'])} 件"
              f"{speed_note}｜涵蓋 {len(spec['members'])} 縣市")

    if not any(g["status"] == "available" for g in groups):
        print("❌ 四個分區都拿不到資料 —— 先跑 src/probe_radar_regions.py 看能力矩陣")
        sys.exit(1)

    geoid_ignored = detect_geoid_ignored(groups)
    for group in groups:
        group["differentiated"] = not geoid_ignored
    if geoid_ignored:
        print("⚠️ 四個分區的序列與 Speed Test 完全相同 —— Radar 忽略了 geoId，"
              "這批是**全國值**。標成 national_only，前端不以此上色。")
    counties = county_records_from_groups(groups,
                                          differentiated=not geoid_ignored)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": args.days,
        "agg_interval": AGG_INTERVAL,
        "source": "Cloudflare Radar (ADM1 / geoId)",
        "granularity": "national_only" if geoid_ignored else "adm1",
        # 讓看檔案的人一眼知道粒度限制，不必回頭翻程式
        "granularity_note": (
            ("Radar 對 quality 端點靜默忽略 geoId：四個分區回的是同一份全國值，"
             "因此 counties 全部標 national_only，不得當成分區或縣市值使用。")
            if geoid_ignored else
            ("Radar 的台灣 ADM1 只有 4 個分區（Taipei / Takao / Fukien＝金門馬祖 / "
             "Taiwan＝其餘 18 縣市），不是 22 個縣市；counties 內每筆的 "
             "is_group_value 標示該數值來自所屬分區，而非該縣市單獨量測。")),
        "metric_ladder": [{k: m[k] for k in
                           ("id", "label_zh", "label_en", "unit",
                            "higher_is_better", "is_speed")}
                          for m in METRIC_LADDER],
        "adm1_groups": groups,
        "counties": counties,
        "summary": build_summary(groups, counties),
    }
    out = Path(args.output)
    atomic_write_json(out, payload, compact=True)
    print(f"✅ {out}  {json.dumps(payload['summary'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
