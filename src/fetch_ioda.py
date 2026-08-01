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

用法:
  python3 src/fetch_ioda.py                  # 抓 28 天、偵測、寫檔
  python3 src/fetch_ioda.py --days 14
  python3 src/fetch_ioda.py --dump-raw       # 印出原始回應（除錯用）

輸出: data/ioda.json（由 workflow 複製到 docs/）
"""
import argparse
import json
import sys
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
    analyze_values,
    trim_series_for_output,
)

IODA_API = "https://api.ioda.inetintel.cc.gatech.edu/v2"
REQUEST_TIMEOUT = 45
DEFAULT_DAYS = 28

# 監測的離島。code 留 None 表示啟動時以 search 動態解析（已知：金門 4209、
# 連江 4210），這樣 IODA 若調整代碼也不會整支壞掉。
ISLANDS = [
    {"id": "kinmen", "search": "Kinmen", "label_zh": "金門", "label_en": "Kinmen",
     "lat": 24.44, "lon": 118.32},
    {"id": "lienchiang", "search": "Lienchiang", "label_zh": "馬祖（連江）",
     "label_en": "Matsu (Lienchiang)", "lat": 26.16, "lon": 119.95},
    {"id": "penghu", "search": "Penghu", "label_zh": "澎湖", "label_en": "Penghu",
     "lat": 23.57, "lon": 119.62},
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

# 離島異常 × 船隻關聯：只看該島周邊這個半徑內的海纜旁滯留船隻。
# 全台範圍找出來的船跟馬祖斷線八竿子打不著，收窄才有判讀價值。
ISLAND_RADIUS_KM = 120.0
CORRELATE_WINDOW_HOURS = 12


def _ts(dt):
    return int(dt.replace(tzinfo=timezone.utc).timestamp()) if dt.tzinfo is None \
        else int(dt.timestamp())


def resolve_region_code(search, session=None, dump=False):
    """以名稱查 IODA 的 region 代碼。找不到回 None。

    回應形狀（實測）：
        {"data": [{"code": "4209", "name": "Kinmen", "type": "region",
                   "attrs": {"country_code": "TW", ...}}]}
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

    for item in payload.get("data") or []:
        attrs = item.get("attrs") or {}
        # 同名地區可能出現在別的國家，必須確認是台灣的
        if attrs.get("country_code") not in (None, "TW"):
            continue
        code = item.get("code")
        if code:
            return str(code)
    print(f"⚠️ [{search}] IODA 沒有對應的 region entity")
    return None


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
    regional_cables = set()
    # 格網中的 cell 是沿線段取樣而來；用 cell 中心判斷可涵蓋端點都在半徑外、
    # 但線路本身穿過島嶼周邊的長海纜。
    for (ci, cj), segments in cable_index.items():
        if haversine_km(lat, lon, (ci + .5) * .1, (cj + .5) * .1) > ISLAND_RADIUS_KM:
            continue
        for segment in segments:
            regional_cables.add(segment[4] if len(segment) > 4 else "未命名海纜")
    nearby = []
    for entry in track_entries:
        vessels = [v for v in (entry.get("vessels") or [])
                   if v.get("lat") is not None and v.get("lon") is not None
                   and haversine_km(lat, lon, v["lat"], v["lon"]) <= ISLAND_RADIUS_KM]
        if vessels:
            nearby.append({**entry, "vessels": vessels})
    return correlate_with_vessels(events, nearby, cable_index,
                                  window_hours=CORRELATE_WINDOW_HOURS,
                                  affected_region=island["label_zh"],
                                  region_confidence="high",
                                  candidate_cables=sorted(regional_cables))


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
        "islands_monitored": len(islands),
        "series_analyzed": sum(len(i["series"]) for i in islands),
        "anomaly_count": len(events),
        "by_severity": by_sev,
        "by_island": by_island,
        "multi_source_corroborated": corroborated,
        "anomalies_with_commercial_or_gov_candidates": leads,
        "latest_anomaly_onset": max((e["onset"] for _, _, e in events), default=None),
    }


def main():
    ap = argparse.ArgumentParser(description="IODA 離島網路可達性監測")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--retain-days", type=int, default=SERIES_RETAIN_DAYS)
    ap.add_argument("--no-correlate", action="store_true")
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
            print(f"   ⚠️ {spec['label_zh']} 沒有任何可用訊號，略過")
            continue
        annotate_corroboration(series)
        for signal in series:
            for event in signal.get("anomalies", []):
                event.update({
                    "affected_region": spec["label_zh"],
                    "region_confidence": "high",
                    "candidate_cables": [],
                })
        islands.append({**spec, "region_code": code, "series": series})

    if not islands:
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

    for island in islands:
        island["series"] = trim_series_for_output(island["series"],
                                                  retain_days=args.retain_days)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": args.days,
        "source": "IODA (Georgia Tech) — BGP / active probing / darknet telescope",
        "islands": islands,
        "summary": build_summary(islands),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"✅ {out}  {json.dumps(payload['summary'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
