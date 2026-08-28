#!/usr/bin/env python3
"""
IODA 離島網路可達性監測 — Taiwan Gray Zone Monitor

Cloudflare Radar 只有國家粒度（`location` 只吃 alpha-2 國碼），量不到金門、馬祖、
澎湖 —— 而這三個離島正是海纜最脆弱、灰色地帶壓力最集中的地方（2023 年 2 月馬祖
兩條海纜先後被中國船隻弄斷，靠微波備援撐了好幾週）。

IODA（Georgia Tech）提供 **region 級**（縣市）的連線中斷訊號，而且是三種互相
獨立的來源：

  bgp          — 該區的 /24 前綴是否從全球路由表消失（控制面）
  ping-slash24 — 分散式主動探測，該區有多少 /24 回應（資料面）
  merit-nt     — darknet telescope，該區的背景輻射流量（被動面）

三者同時下掉才是真中斷；只有一個動多半是量測雜訊。這比從單一 GitHub runner
ping 過去可信得多（單點量測分不出「島斷了」還是「runner 網路抖動」）。

**馬祖的判讀要特別小心**：它有微波備援，海纜斷掉時不會完全失聯，而是嚴重降速。
因此偵測用的是「相對自身基線的持續性偏離」而非「歸零」——與 Cloudflare Radar
那條線共用 `anomaly_detect` 的同一套判讀。

Region 代碼由 `/entities/query` 動態解析（金門 4209、連江 4210、澎湖 …），不寫死。

**全台 22 縣市**（`COUNTIES`）同樣有 region 級訊號，因此除了三座離島的完整序列，
本檔另外輸出一份精簡的 `counties`，給前端的縣市色塊地圖用。Cloudflare Radar 的
縣市級指標（`fetch_radar_counties.py`）要 token 且不保證每個縣市都有樣本；IODA
不需要憑證，是縣市粒度的保底來源。三座離島不重抓——直接沿用上面已經算好的序列。

用法:
  python3 src/fetch_ioda.py                  # 抓 28 天、偵測、寫檔
  python3 src/fetch_ioda.py --days 14
  python3 src/fetch_ioda.py --no-counties    # 只做三離島（舊行為）
  python3 src/fetch_ioda.py --dump-raw       # 印出原始回應（除錯用）

輸出: data/ioda.json（由 workflow 複製到 docs/）
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = BASE_DIR / "docs"
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from anomaly_detect import (  # noqa: E402
    SERIES_RETAIN_DAYS,
    _parse_ts,
    analyze_values,
    trim_series_for_output,
)
from io_utils import atomic_write_json, load_json  # noqa: E402

IODA_API = "https://api.ioda.inetintel.cc.gatech.edu/v2"
REQUEST_TIMEOUT = 45
DEFAULT_DAYS = 28

# 監測的離島。code 留 None 表示啟動時以 search 動態解析（已知：金門 4209、
# 連江 4210），這樣 IODA 若調整代碼也不會整支壞掉。
ISLANDS = [
    {"id": "kinmen", "iso": "TW-KIN", "search": "Kinmen", "label_zh": "金門",
     "label_en": "Kinmen", "lat": 24.44, "lon": 118.32},
    {"id": "lienchiang", "iso": "TW-LIE", "search": "Lienchiang",
     "label_zh": "馬祖（連江）", "label_en": "Matsu (Lienchiang)",
     "lat": 26.16, "lon": 119.95},
    {"id": "penghu", "iso": "TW-PEN", "search": "Penghu", "label_zh": "澎湖",
     "label_en": "Penghu", "lat": 23.57, "lon": 119.62},
]

# 三種獨立訊號。全部是「越低越糟」，所以 direction 一律 drop。
#
# `min_level`：低計數守衛。實測 IODA 對離島這種小區域，darknet 背景流量的值域
# 就在個位數（澎湖首點值＝1.0），百分比門檻在這種尺度下毫無意義（2→1 就是
# -50%），一週噴出 12 件假異常。BGP 前綴數（400~500）與可達 /24 數（50~90）
# 則有足夠的量級可以用相對變化判讀。
DATASOURCES = [
    {"id": "bgp", "label_zh": "BGP 可見前綴", "label_en": "BGP visible prefixes",
     "min_level": 20},
    {"id": "ping-slash24", "label_zh": "主動探測可達 /24",
     "label_en": "Active probing (/24 reachable)", "min_level": 10},
    {"id": "merit-nt", "label_zh": "Darknet 背景流量",
     "label_en": "Darknet background traffic", "min_level": 20},
]

# ── 全台 22 縣市 ───────────────────────────────────────────────────────────
# IODA 的 region entity 用英文名查，而同一個縣市可能有好幾種寫法（"Taipei" /
# "Taipei City"、"New Taipei" / "Taipei County"），因此每個縣市給一串候選名，
# 依序試到查得到為止。`iso` 是對到 docs/tw_counties.geojson 的鍵（同一份 ISO
# 3166-2 代碼），前端才能把訊號畫到正確的色塊上。
# lat/lon 取自縣市界圖最大島的形心，只作地圖聚焦與離島船隻關聯用。
COUNTIES = [
    {"iso": "TW-CHA", "search": ["Changhua", "Changhua County"], "label_zh": "彰化縣", "label_en": "Changhua", "lat": 23.9634, "lon": 120.5193},
    {"iso": "TW-CYI", "search": ["Chiayi City"], "label_zh": "嘉義市", "label_en": "Chiayi City", "lat": 23.4825, "lon": 120.4442},
    {"iso": "TW-CYQ", "search": ["Chiayi County", "Chiayi"], "label_zh": "嘉義縣", "label_en": "Chiayi County", "lat": 23.4452, "lon": 120.4981},
    {"iso": "TW-HSQ", "search": ["Hsinchu County", "Hsinchu"], "label_zh": "新竹縣", "label_en": "Hsinchu County", "lat": 24.6936, "lon": 121.1299},
    {"iso": "TW-HSZ", "search": ["Hsinchu City"], "label_zh": "新竹市", "label_en": "Hsinchu City", "lat": 24.7794, "lon": 120.9583},
    {"iso": "TW-HUA", "search": ["Hualien", "Hualien County"], "label_zh": "花蓮縣", "label_en": "Hualien", "lat": 23.8006, "lon": 121.3766},
    {"iso": "TW-ILA", "search": ["Yilan", "Ilan", "Yilan County"], "label_zh": "宜蘭縣", "label_en": "Yilan", "lat": 24.5995, "lon": 121.6504},
    {"iso": "TW-KEE", "search": ["Keelung", "Keelung City", "Chilung"], "label_zh": "基隆市", "label_en": "Keelung", "lat": 25.1248, "lon": 121.7334},
    {"iso": "TW-KHH", "search": ["Kaohsiung", "Kaohsiung City"], "label_zh": "高雄市", "label_en": "Kaohsiung", "lat": 22.9612, "lon": 120.587},
    {"iso": "TW-KIN", "search": ["Kinmen", "Quemoy"], "label_zh": "金門縣", "label_en": "Kinmen", "lat": 24.4501, "lon": 118.3874},
    {"iso": "TW-LIE", "search": ["Lienchiang", "Lienkiang", "Matsu"], "label_zh": "連江縣（馬祖）", "label_en": "Lienchiang (Matsu)", "lat": 26.1567, "lon": 119.934},
    {"iso": "TW-MIA", "search": ["Miaoli", "Miaoli County"], "label_zh": "苗栗縣", "label_en": "Miaoli", "lat": 24.4992, "lon": 120.9904},
    {"iso": "TW-NAN", "search": ["Nantou", "Nantou County"], "label_zh": "南投縣", "label_en": "Nantou", "lat": 23.8565, "lon": 120.9472},
    {"iso": "TW-NWT", "search": ["New Taipei", "New Taipei City", "Taipei County"], "label_zh": "新北市", "label_en": "New Taipei", "lat": 25.0213, "lon": 121.5742},
    {"iso": "TW-PEN", "search": ["Penghu", "Pescadores"], "label_zh": "澎湖縣", "label_en": "Penghu", "lat": 23.5583, "lon": 119.5999},
    {"iso": "TW-PIF", "search": ["Pingtung", "Pingtung County"], "label_zh": "屏東縣", "label_en": "Pingtung", "lat": 22.3942, "lon": 120.7226},
    {"iso": "TW-TAO", "search": ["Taoyuan", "Taoyuan City", "Taoyuan County"], "label_zh": "桃園市", "label_en": "Taoyuan", "lat": 24.8935, "lon": 121.2951},
    {"iso": "TW-TNN", "search": ["Tainan", "Tainan City"], "label_zh": "臺南市", "label_en": "Tainan", "lat": 23.1696, "lon": 120.3092},
    {"iso": "TW-TPE", "search": ["Taipei City", "Taipei"], "label_zh": "臺北市", "label_en": "Taipei", "lat": 25.0727, "lon": 121.5612},
    {"iso": "TW-TTT", "search": ["Taitung", "Taitung County"], "label_zh": "臺東縣", "label_en": "Taitung", "lat": 22.907, "lon": 121.0713},
    {"iso": "TW-TXG", "search": ["Taichung", "Taichung City"], "label_zh": "臺中市", "label_en": "Taichung", "lat": 24.2541, "lon": 120.9511},
    {"iso": "TW-YUN", "search": ["Yunlin", "Yunlin County"], "label_zh": "雲林縣", "label_en": "Yunlin", "lat": 23.6517, "lon": 120.4211},
]

# region 代碼查詢一次就快取起來：22 縣市 × 每 2 小時重查是白打的請求，
# 而 IODA 的 region 代碼幾乎不會變。
REGION_CODE_CACHE = DATA_DIR / "ioda_region_codes.json"
REGION_CACHE_DAYS = 30

# 縣市地圖只需要縮圖，不需要逐點：3 小時一格 × 7 天 ≈ 56 點／縣市。
# （這個檔每 2 小時 commit 一次，22 縣市 × 3 訊號的完整序列會把 repo 撐大。）
COUNTY_BUCKET_HOURS = 3
COUNTY_RETAIN_DAYS = 7
# 色階用的主訊號優先序：主動探測最貼近「使用者連得上嗎」
COUNTY_PRIMARY_ORDER = ["ping-slash24", "bgp", "merit-nt"]
# 「目前仍異常」的認定：最後一筆異常結束於這麼多小時內
ONGOING_WINDOW_HOURS = 6
# 併發抓取的執行緒數。22 縣市 × 3 訊號 = 66 次請求，循序跑會讓這個 job 拖很久；
# IODA 是公共服務，開太多併發不禮貌也容易被限速。
FETCH_WORKERS = 4

# 離島異常 × 船隻關聯：只看該島周邊這個半徑內的海纜旁滯留船隻。
# 全台範圍找出來的船跟馬祖斷線八竿子打不著，收窄才有判讀價值。
ISLAND_RADIUS_KM = 120.0
CORRELATE_WINDOW_HOURS = 12


def _ts(dt):
    return int(dt.replace(tzinfo=timezone.utc).timestamp()) if dt.tzinfo is None \
        else int(dt.timestamp())


def resolve_region_code(search, session=None, dump=False, prefer=None):
    """以名稱查 IODA 的 region 代碼。找不到回 None。

    回應形狀（實測）：
        {"data": [{"code": "4209", "name": "Kinmen", "type": "region",
                   "attrs": {"country_code": "TW", ...}}]}

    `prefer` 給名稱完全相符時的優先權：搜尋「Chiayi」會同時撈到嘉義市與嘉義縣，
    只取第一筆會把兩個縣市畫成同一個。有 prefer 時先找完全相符的名稱。
    """
    session = session or requests
    try:
        resp = session.get(f"{IODA_API}/entities/query",
                           params={"entityType": "region", "search": search},
                           timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        print(f"⚠️ [{search}] region 查詢失敗: {e}")
        return None
    if resp.status_code != 200:
        print(f"⚠️ [{search}] region 查詢 HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    if dump:
        print(json.dumps(payload, ensure_ascii=False)[:800])

    candidates = []
    for item in payload.get("data") or []:
        attrs = item.get("attrs") or {}
        # 同名地區可能出現在別的國家，必須確認是台灣的
        if attrs.get("country_code") not in (None, "TW"):
            continue
        code = item.get("code")
        if code:
            candidates.append((str(item.get("name") or ""), str(code)))
    if not candidates:
        print(f"⚠️ [{search}] IODA 沒有對應的 region entity")
        return None
    if prefer:
        wanted = {p.strip().lower() for p in prefer}
        for name, code in candidates:
            if name.strip().lower() in wanted:
                return code
    return candidates[0][1]


def parse_signal_payload(payload):
    """從 IODA signals 回應解析出 (timestamps_iso, values)。

    回應大致為 `data: [[ {from, until, step, values: [...] } ]]`（外層一個
    list-of-list）。IODA 的訊號序列給的是起點＋step，不是逐點時間戳，因此時間戳
    要自己算。這裡對層數與欄位名都放寬處理——沙箱連不上 IODA，無法對真實回應
    驗證，寧可多接受幾種形狀也不要整支炸掉（`--dump-raw` 可印出原始回應）。
    """
    data = (payload or {}).get("data")
    node = data
    # 剝掉外層的 list 包裝，直到看到 dict
    for _ in range(4):
        if isinstance(node, list):
            if not node:
                return [], []
            node = node[0]
        else:
            break
    if not isinstance(node, dict):
        return [], []

    values = node.get("values")
    if not isinstance(values, list) or not values:
        return [], []

    start = node.get("from") or node.get("start") or node.get("fromTime")
    step = node.get("step") or node.get("interval") or 3600
    try:
        start = int(start)
        step = int(step) or 3600
    except (TypeError, ValueError):
        return [], []

    timestamps, cleaned = [], []
    for i, v in enumerate(values):
        t = datetime.fromtimestamp(start + i * step, tz=timezone.utc)
        timestamps.append(t.isoformat().replace("+00:00", "Z"))
        try:
            cleaned.append(None if v is None else float(v))
        except (TypeError, ValueError):
            cleaned.append(None)
    return timestamps, cleaned


def fetch_signal(code, datasource, start, end, session=None, dump=False):
    """抓某個 region 的某一種訊號。失敗回 None。"""
    session = session or requests
    url = f"{IODA_API}/signals/raw/region/{code}"
    try:
        resp = session.get(url,
                           params={"from": _ts(start), "until": _ts(end),
                                   "datasource": datasource},
                           timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        print(f"⚠️ [{code}/{datasource}] 請求失敗: {e}")
        return None
    if resp.status_code != 200:
        print(f"⚠️ [{code}/{datasource}] HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    try:
        payload = resp.json()
    except ValueError as e:
        print(f"⚠️ [{code}/{datasource}] 回應不是 JSON: {e}")
        return None
    if dump:
        print(json.dumps(payload, ensure_ascii=False)[:1200])

    timestamps, values = parse_signal_payload(payload)
    if not timestamps:
        print(f"⚠️ [{code}/{datasource}] 回應沒有可用的訊號序列"
              f"（--dump-raw 可看原始回應）")
        return None
    return {"timestamps": timestamps, "values": values}


def corroboration(island_series):
    """同一座島上，有幾種**獨立**訊號在同一時間出現異常。

    三種來源的原理完全不同（路由 / 主動探測 / 被動流量），任一種單獨下掉都可能
    是該來源自己的量測問題；兩種以上同時下掉才值得當成真的連線中斷。
    """
    windows = []
    for s in island_series:
        for e in s.get("anomalies", []):
            windows.append((s["datasource"], e["onset"], e["end"]))
    counts = {}
    for ds, onset, end in windows:
        overlap = {ds}
        for ds2, onset2, end2 in windows:
            if ds2 == ds:
                continue
            # 時間窗有重疊就算互相印證
            if onset2 <= end and end2 >= onset:
                overlap.add(ds2)
        counts[(onset, end)] = max(counts.get((onset, end), 0), len(overlap))
    return counts


def annotate_corroboration(island_series):
    """把「幾種訊號同時異常」寫回每個事件。"""
    counts = corroboration(island_series)
    for s in island_series:
        for e in s.get("anomalies", []):
            n = counts.get((e["onset"], e["end"]), 1)
            e["corroborating_sources"] = n
            # 只有單一來源的事件降一級——多半是該來源自己的量測問題
            if n < 2 and e.get("severity") == "critical":
                e["severity"] = "high"
                e["severity_downgraded"] = "single_source"
    return island_series


def correlate_island_vessels(island, events, track_entries, cable_index):
    """離島異常 × 該島周邊的海纜旁滯留船隻（收窄到 ISLAND_RADIUS_KM）。"""
    try:
        from fetch_cloudflare_radar import correlate_with_vessels
        from geo_utils import haversine_km
    except Exception as e:
        print(f"⚠️ 無法載入船隻關聯模組: {e}")
        return events

    lat, lon = island["lat"], island["lon"]
    nearby = []
    for entry in track_entries:
        vessels = [v for v in (entry.get("vessels") or [])
                   if v.get("lat") is not None and v.get("lon") is not None
                   and haversine_km(lat, lon, v["lat"], v["lon"]) <= ISLAND_RADIUS_KM]
        if vessels:
            nearby.append({**entry, "vessels": vessels})
    return correlate_with_vessels(events, nearby, cable_index,
                                  window_hours=CORRELATE_WINDOW_HOURS)


# ── 縣市級（22 縣市精簡輸出）────────────────────────────────────────────────

def resolve_county_codes(session=None, refresh=False,
                         cache_path=REGION_CODE_CACHE, counties=None, dump=False):
    """ISO → IODA region code。優先讀快取（30 天），過期才重查。

    查不到的縣市不會覆蓋掉快取裡已知的值——IODA 的搜尋偶爾會抽風，
    寧可用上一次查到的代碼，也不要讓整個縣市在地圖上憑空消失。
    """
    counties = counties or COUNTIES
    cache = load_json(cache_path, {}, label="IODA region 代碼快取",
                      expect_type=dict)
    known = dict(cache.get("codes") or {})
    resolved_at = _parse_ts(cache.get("resolved_at"))
    fresh = resolved_at and (datetime.now(timezone.utc) - resolved_at
                             < timedelta(days=REGION_CACHE_DAYS))
    if known and fresh and not refresh:
        print(f"🗂️  region 代碼快取命中（{len(known)} 縣市）")
        return known

    for county in counties:
        for name in county["search"]:
            code = resolve_region_code(name, session, dump=dump,
                                       prefer=county["search"])
            if code:
                known[county["iso"]] = code
                break
    atomic_write_json(cache_path, {
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "codes": known,
    })
    print(f"🗂️  解析出 {len(known)}/{len(counties)} 個縣市的 region 代碼")
    return known


def downsample_county_series(timestamps, values,
                             bucket_hours=COUNTY_BUCKET_HOURS,
                             retain_days=COUNTY_RETAIN_DAYS):
    """逐時序列 → 每 bucket_hours 一格的中位數（只留最近 retain_days）。

    偵測仍吃完整序列，壓的只是寫進 JSON 給前端畫縮圖的那份。中位數而非平均：
    一個量測缺口造成的 0 不該把整格拉低。
    """
    keep = int(retain_days * 24)
    timestamps = list(timestamps)[-keep:]
    values = list(values)[-keep:]
    out_ts, out_vals = [], []
    for i in range(0, len(timestamps), bucket_hours):
        chunk = [v for v in values[i:i + bucket_hours] if v is not None]
        out_ts.append(timestamps[i])
        out_vals.append(round(sorted(chunk)[len(chunk) // 2], 2) if chunk else None)
    return out_ts, out_vals


def classify_county_level(series_list, now=None):
    """縣市色階：unknown（無訊號）／alert（仍在進行）／watch（近期有）／normal。"""
    if not series_list:
        return "unknown"
    now = now or datetime.now(timezone.utc)
    events = [e for s in series_list for e in (s.get("anomalies") or [])]
    if not events:
        return "normal"
    for event in events:
        end = _parse_ts(event.get("end") or event.get("onset"))
        if end and (now - end) <= timedelta(hours=ONGOING_WINDOW_HOURS):
            return "alert"
    return "watch"


def _latest_value(series):
    for v in reversed(series.get("values") or []):
        if v is not None:
            return v
    return None


def compact_county_record(county, code, series_list, now=None):
    """縣市的精簡輸出（純函式）。完整序列只給三離島，其餘只留縮圖與事件摘要。"""
    events = [e for s in series_list for e in (s.get("anomalies") or [])]
    events.sort(key=lambda e: e.get("onset") or "", reverse=True)
    primary = None
    for wanted in COUNTY_PRIMARY_ORDER:
        primary = next((s for s in series_list if s.get("datasource") == wanted),
                       None)
        if primary:
            break
    if primary is None and series_list:
        primary = series_list[0]

    record = {
        "iso": county["iso"],
        "label_zh": county["label_zh"],
        "label_en": county["label_en"],
        "lat": county["lat"],
        "lon": county["lon"],
        "region_code": code,
        "status": "available" if series_list else "unavailable",
        "level": classify_county_level(series_list, now=now),
        "anomaly_count": len(events),
        "max_corroborating_sources": max(
            (e.get("corroborating_sources") or 1 for e in events), default=0),
        "signals": [{
            "datasource": s.get("datasource"),
            "label_zh": s.get("label_zh"),
            "label_en": s.get("label_en"),
            "points": s.get("points"),
            "baseline_coverage": s.get("baseline_coverage"),
            "anomaly_count": len(s.get("anomalies") or []),
            "latest": _latest_value(s),
        } for s in series_list],
        "latest_anomaly": ({
            "onset": events[0].get("onset"), "end": events[0].get("end"),
            "severity": events[0].get("severity"),
            "max_deviation_pct": events[0].get("max_deviation_pct"),
            "corroborating_sources": events[0].get("corroborating_sources"),
        } if events else None),
    }
    if primary:
        ts, vals = downsample_county_series(primary.get("timestamps") or [],
                                            primary.get("values") or [])
        record["primary"] = {"datasource": primary.get("datasource"),
                             "timestamps": ts, "values": vals,
                             "bucket_hours": COUNTY_BUCKET_HOURS}
    return record


def fetch_county_series(county, code, start, end, dump=False):
    """抓一個縣市的三種訊號並跑偵測。回傳已標註互相印證的 series list。

    每個縣市自己開一個 requests.Session：這支在執行緒池裡跑，
    共用一個 session 不值得為了省幾個 TCP 連線去賭 thread-safety。
    """
    session = requests.Session()
    series = []
    for ds in DATASOURCES:
        sig = fetch_signal(code, ds["id"], start, end, session, dump=dump)
        if sig is None:
            continue
        out = analyze_values(sig["timestamps"], sig["values"], direction="drop",
                             min_baseline_level=ds.get("min_level", 0),
                             resample=True)
        out.update({"datasource": ds["id"], "label_zh": ds["label_zh"],
                    "label_en": ds["label_en"]})
        series.append(out)
    if series:
        annotate_corroboration(series)
    return series


def collect_counties(codes, start, end, island_series_by_iso=None,
                     workers=FETCH_WORKERS, counties=None, dump=False):
    """22 縣市的精簡記錄。三離島直接沿用已算好的序列，不重打 API。"""
    counties = counties or COUNTIES
    island_series_by_iso = island_series_by_iso or {}
    todo = []
    records = {}
    for county in counties:
        iso = county["iso"]
        code = codes.get(iso)
        if iso in island_series_by_iso:
            records[iso] = compact_county_record(
                county, code, island_series_by_iso[iso])
            continue
        if not code:
            records[iso] = {**compact_county_record(county, None, []),
                            "error_reason": "region_not_found"}
            continue
        todo.append((county, code))

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            results = pool.map(
                lambda item: (item[0], item[1],
                              fetch_county_series(item[0], item[1], start, end,
                                                  dump=dump)),
                todo)
            for county, code, series in results:
                record = compact_county_record(county, code, series)
                if not series:
                    record["error_reason"] = "signals_unavailable"
                records[county["iso"]] = record
    return [records[c["iso"]] for c in counties]


def build_summary(islands):
    events = [(i["id"], s["datasource"], e)
              for i in islands for s in i["series"] for e in s.get("anomalies", [])]
    by_sev, by_island = {}, {}
    for iid, _, e in events:
        by_sev[e["severity"]] = by_sev.get(e["severity"], 0) + 1
        by_island[iid] = by_island.get(iid, 0) + 1
    corroborated = sum(1 for _, _, e in events
                       if (e.get("corroborating_sources") or 1) >= 2)
    leads = sum(1 for _, _, e in events
                if (e.get("candidate_summary") or {}).get("commercial")
                or (e.get("candidate_summary") or {}).get("gov"))
    return {
        "islands_configured": len(islands),
        "islands_monitored": sum(1 for i in islands
                                 if i.get("status", "available" if i.get("series") else
                                          "unavailable") == "available"),
        "islands_unavailable": sum(1 for i in islands
                                   if i.get("status", "available" if i.get("series") else
                                            "unavailable") != "available"),
        "series_analyzed": sum(len(i["series"]) for i in islands),
        "anomaly_count": len(events),
        "by_severity": by_sev,
        "by_island": by_island,
        "multi_source_corroborated": corroborated,
        "anomalies_with_commercial_or_gov_candidates": leads,
        "latest_anomaly_onset": max((e["onset"] for _, _, e in events), default=None),
    }


def build_county_summary(counties):
    """縣市層的摘要。`by_level` 直接對應地圖色階，前端不必自己數。"""
    by_level = {}
    for c in counties:
        by_level[c["level"]] = by_level.get(c["level"], 0) + 1
    return {
        "counties_total": len(counties),
        "counties_monitored": sum(1 for c in counties
                                  if c["status"] == "available"),
        "by_level": by_level,
        "anomaly_count": sum(c["anomaly_count"] for c in counties),
        "counties_with_ongoing": sum(1 for c in counties if c["level"] == "alert"),
    }


def main():
    ap = argparse.ArgumentParser(description="IODA 離島網路可達性監測")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--retain-days", type=int, default=SERIES_RETAIN_DAYS)
    ap.add_argument("--no-correlate", action="store_true")
    ap.add_argument("--no-counties", action="store_true",
                    help="只做三離島（舊行為），不抓 22 縣市")
    ap.add_argument("--refresh-region-codes", action="store_true",
                    help="忽略快取，重新向 IODA 解析縣市 region 代碼")
    ap.add_argument("--workers", type=int, default=FETCH_WORKERS,
                    help=f"縣市抓取的併發數（預設 {FETCH_WORKERS}）")
    ap.add_argument("--dump-raw", action="store_true", help="印出原始 API 回應")
    ap.add_argument("-o", "--output", default=str(DATA_DIR / "ioda.json"))
    args = ap.parse_args()

    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=args.days)
    session = requests.Session()

    islands = []
    for spec in ISLANDS:
        code = resolve_region_code(spec["search"], session, dump=args.dump_raw)
        if not code:
            islands.append({**spec, "region_code": None, "status": "unavailable",
                            "error_reason": "region_not_found", "series": []})
            continue
        print(f"🏝️  {spec['label_zh']}（region {code}）")
        series = []
        for ds in DATASOURCES:
            sig = fetch_signal(code, ds["id"], start, end, session,
                               dump=args.dump_raw)
            if sig is None:
                continue
            # IODA 的原始解析度是 5~10 分鐘（不是逐時），必須重採樣：
            # 否則「一個點＝一小時」的持續時數會差 12 倍，連續 2 點的門檻也
            # 只剩 10 分鐘，過於敏感。
            out = analyze_values(sig["timestamps"], sig["values"], direction="drop",
                                 min_baseline_level=ds.get("min_level", 0),
                                 resample=True)
            out.update({"datasource": ds["id"],
                        "label_zh": ds["label_zh"], "label_en": ds["label_en"]})
            series.append(out)
            n = len(out["anomalies"])
            print(f"   ↳ {ds['label_zh']}: {out['points']} 點｜基線覆蓋 "
                  f"{out['baseline_coverage']:.0%}｜異常 {n} 件"
                  + (f" {[e['severity'] for e in out['anomalies']]}" if n else ""))
        if not series:
            print(f"   ⚠️ {spec['label_zh']} 沒有任何可用訊號")
            islands.append({**spec, "region_code": code, "status": "unavailable",
                            "error_reason": "signals_unavailable", "series": []})
        else:
            annotate_corroboration(series)
            islands.append({**spec, "region_code": code, "status": "available",
                            "error_reason": None, "series": series})

    if not any(i["series"] for i in islands):
        print("❌ 沒有任何離島訊號抓取成功")
        sys.exit(1)

    if not args.no_correlate and any(s["anomalies"] for i in islands
                                     for s in i["series"]):
        print("🚢 比對離島周邊海纜旁滯留船隻 …")
        try:
            from fetch_cloudflare_radar import build_cable_index, load_track_entries
            from geofence import load_cable_segments
            cable_index = build_cable_index(load_cable_segments())
            entries = load_track_entries()
            for island in islands:
                for s in island["series"]:
                    if s["anomalies"]:
                        s["anomalies"] = correlate_island_vessels(
                            island, s["anomalies"], entries, cable_index)
            print(f"   ↳ 軌跡快照 {len(entries)} 筆，半徑 {ISLAND_RADIUS_KM:.0f}km")
        except Exception as e:
            print(f"⚠️ 船隻關聯失敗（不影響可達性偵測）: {e}")

    # 縣市層要在 trim 之前算：trim 會把序列砍到 14 天並降精度，
    # 縣市縮圖自己有一套降採樣，吃完整序列才不會被砍兩次。
    counties = []
    if not args.no_counties:
        print(f"🗺️  抓取全台 {len(COUNTIES)} 縣市的可達性訊號 …")
        island_series_by_iso = {i["iso"]: i["series"] for i in islands
                                if i.get("iso") and i.get("series")}
        codes = resolve_county_codes(session, refresh=args.refresh_region_codes,
                                     dump=args.dump_raw)
        counties = collect_counties(codes, start, end,
                                    island_series_by_iso=island_series_by_iso,
                                    workers=args.workers, dump=args.dump_raw)
        monitored = sum(1 for c in counties if c["status"] == "available")
        print(f"   ↳ {monitored}/{len(counties)} 個縣市有訊號｜"
              f"異常 {sum(c['anomaly_count'] for c in counties)} 件")

    for island in islands:
        island["series"] = trim_series_for_output(island["series"],
                                                  retain_days=args.retain_days)

    summary = build_summary(islands)
    if counties:
        summary["counties"] = build_county_summary(counties)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": args.days,
        "source": "IODA (Georgia Tech) — BGP / active probing / darknet telescope",
        "islands": islands,
        "counties": counties,
        "summary": summary,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"✅ {out}  {json.dumps(payload['summary'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
