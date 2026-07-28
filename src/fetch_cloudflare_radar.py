#!/usr/bin/env python3
"""
Cloudflare Radar 網路流量異常偵測 — Taiwan Gray Zone Monitor

海纜被破壞的**後果**會出現在網路層：流量掉下去、延遲跳上去。這支腳本抓
Cloudflare Radar 的台灣流量／延遲時間序列，做季節性去趨勢後的異常偵測，並把
異常發生前的時間窗拿去比對「當時有哪些船在海纜旁低速滯留」——把行為訊號和
實際後果接起來，這才是灰色地帶判讀，不只是網路儀表板。

偵測方法（皆為純函式，可單測）：
1. **同時段基線**（`hour_of_week_baseline`）：流量有很強的日／週週期，因此基線
   取「同一星期幾＋同一小時」其他週的中位數（leave-one-out，異常點不會污染
   自己的基線）。資料不足兩週時退回 24 點滾動中位數。
2. **穩健 z 分數**：殘差除以 MAD×1.4826（不用標準差——一次大掉點就會把標準差
   撐大到什麼都偵測不到）。
3. **連續性 + 相對幅度雙門檻**（`detect_anomalies`）：單一時段的尖刺多半是量測
   雜訊，要求連續 ≥2 個時段、且相對基線掉幅 ≥10% 才成案。海纜中斷的特徵是
   「持續性的位階下移」而非瞬間尖刺，因此嚴重度由掉幅 × 持續時數決定。

環境變數（token 需具備 Radar Read 權限）:
  CLOUDFLARE_API_TOKEN   — Cloudflare API token（必填）
                           別名：CLAUDEFARETOKEN / CLAUDEFLARETOKEN / CF_API_TOKEN
  CLOUDFLARE_ACCOUNT_ID  — 帳號 ID（選填；Radar 端點不需要，僅記錄用）
                           別名：CLAUDEFLAREACCOUNTID / CLAUDEFAREACCOUNTID

用法:
  python3 src/fetch_cloudflare_radar.py                # 抓 28 天、偵測、寫檔
  python3 src/fetch_cloudflare_radar.py --days 14
  python3 src/fetch_cloudflare_radar.py --no-correlate # 只做流量偵測，不比對船隻

輸出: data/cf_radar.json
"""
import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = BASE_DIR / "docs"
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from geo_utils import point_to_segment_distance_km  # noqa: E402
# 偵測邏輯與資料來源無關，與 fetch_ioda.py 共用同一套（見 anomaly_detect.py）。
# 這裡沿用原本的名稱重新匯出，既有呼叫端與測試不必改。
from anomaly_detect import (  # noqa: E402,F401
    BASELINE_MIN_SAMPLES, FALLBACK_MIN_POINTS, FALLBACK_WINDOW,
    MIN_CONSECUTIVE, MIN_DEVIATION_PCT, OUTPUT_PRECISION, SCALE_FLOOR_RATIO,
    SERIES_RETAIN_DAYS, SEVERITY_CRITICAL_HOURS, SEVERITY_CRITICAL_PCT,
    SEVERITY_HIGH_PCT, SEVERITY_HIGH_Z, Z_THRESHOLD,
    _parse_ts, analyze_values, classify_severity, detect_anomalies,
    effective_scale,
    hour_of_week_baseline, mask_reporting_gaps, robust_scale,
    trim_series_for_output,
)

CF_API_BASE = "https://api.cloudflare.com/client/v4"
REQUEST_TIMEOUT = 30

TOKEN_ENV_NAMES = ("CLOUDFLARE_API_TOKEN", "CLAUDEFARETOKEN",
                   "CLAUDEFLARETOKEN", "CF_API_TOKEN")
ACCOUNT_ENV_NAMES = ("CLOUDFLARE_ACCOUNT_ID", "CLAUDEFLAREACCOUNTID",
                     "CLAUDEFAREACCOUNTID", "CF_ACCOUNT_ID")

DEFAULT_DAYS = 28   # 偵測視窗；輸出只留 SERIES_RETAIN_DAYS 天（anomaly_detect）
AGG_INTERVAL = "1h"

# 抓哪些序列。optional=True 者抓不到就跳過（Radar 對小行政區可能沒有足夠樣本）
SERIES_SPECS = [
    {
        "id": "tw_netflows",
        "label": "台灣整體網路流量 (netflows)",
        "path": "radar/netflows/timeseries",
        "params": {"location": "TW"},
        "direction": "drop",
    },
    {
        "id": "tw_http",
        "label": "台灣 HTTP 請求量",
        "path": "radar/http/timeseries",
        "params": {"location": "TW"},
        "direction": "drop",
    },
    {
        # 中華電信 HiNet：馬祖、金門的對外連線都掛在這個 ASN 底下
        "id": "as3462_netflows",
        "label": "中華電信 HiNet (AS3462) 流量",
        "path": "radar/netflows/timeseries",
        "params": {"asn": "3462"},
        "direction": "drop",
    },
    {
        "id": "tw_latency",
        "label": "台灣連線延遲 (IQI p50)",
        "path": "radar/quality/iqi/timeseries_groups",
        "params": {"location": "TW", "metric": "latency"},
        "direction": "spike",   # 延遲是越高越糟
    },
]

# ⚠️ `location` 只接受 **ISO 3166-1 alpha-2 國家碼**（TW）。曾經試過用 3166-2 的
# 行政區碼（TW-LIE 連江縣／TW-KIN 金門縣）取得馬祖、金門的離島粒度，Radar 直接
# 回 HTTP 400：
#     Invalid location codes. Must be valid alpha-2 location codes
# Radar 沒有縣市粒度，別再加回來。離島層級的中斷偵測要另尋來源（IODA 有
# region-level 的 BGP／主動探測資料），或退而求其次看 AS3462 這種承載離島對外
# 連線的 ASN。

# ── 船隻關聯參數 ────────────────────────────────────────────────────────────
CORRELATE_WINDOW_HOURS = 12   # 異常開始前多久內的船隻行為算相關
CORRELATE_CABLE_KM = 5.0      # 距海纜多近
CORRELATE_MAX_SPEED_KN = 5.0  # 多慢算滯留
CORRELATE_MAX_VESSELS = 10
CABLE_CELL_DEG = 0.1          # 海纜格網索引的格子大小

# 候選船隻的排序優先級：能真正弄壞海纜的船型排前面。
# 台灣海峽的海纜旁永遠躺著幾百艘小漁船，若只按距離排序，名單會被漁船洗掉——
# 這個權重和 analyze_suspicious 的 vessel type multiplier 是同一套判斷。
CORRELATE_TYPE_PRIORITY = {
    "cargo": 0, "tanker": 0, "lng": 0,
    "coastguard": 1, "msa": 1, "rescue": 1, "research": 1,
    "dredger": 0,
    "fishing": 2,
}
CORRELATE_DEFAULT_PRIORITY = 2


def _get_env(*names):
    for name in names:
        val = os.environ.get(name)
        if val:
            return val.strip()
    return None


# ── API 取得 ────────────────────────────────────────────────────────────────

def _to_float(v):
    """Radar 的數值常以字串回傳（"1.234"），統一轉 float；轉不動回 None。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # 濾掉 NaN


def parse_timeseries_payload(payload):
    """從 Radar 回應解析出 (timestamps, values)。

    Radar 各端點的殼略有差異（`serie_0`、`serie_x`、IQI 的 p25/p50/p75），
    因此不硬綁單一 key：找出 result 底下第一個同時具備 timestamps 和某個
    數值陣列的 dict。IQI 這種多分位的優先取 p50。
    """
    result = (payload or {}).get("result") or {}
    candidates = []
    if isinstance(result, dict):
        for key, val in result.items():
            if key == "meta" or not isinstance(val, dict):
                continue
            if "timestamps" in val:
                candidates.append((key, val))
    if not candidates:
        return [], []

    # serie_0 優先，其餘按 key 排序，結果才穩定
    candidates.sort(key=lambda kv: (kv[0] != "serie_0", kv[0]))
    _, serie = candidates[0]
    timestamps = list(serie.get("timestamps") or [])

    for value_key in ("p50", "values", "value", "p75", "p25"):
        raw = serie.get(value_key)
        if isinstance(raw, list) and raw:
            values = [_to_float(v) for v in raw]
            n = min(len(timestamps), len(values))
            return timestamps[:n], values[:n]

    # 沒有已知的值欄位 → 取第一個長度相符的數值陣列
    for key, raw in serie.items():
        if key == "timestamps" or not isinstance(raw, list):
            continue
        if len(raw) == len(timestamps):
            return timestamps, [_to_float(v) for v in raw]
    return [], []


def fetch_series(spec, token, days=DEFAULT_DAYS, session=None):
    """抓一條時間序列。失敗回 None（optional 的序列由呼叫端決定要不要吵）。"""
    session = session or requests
    params = {
        "dateRange": f"{days}d",
        "aggInterval": AGG_INTERVAL,
        "format": "json",
        **spec.get("params", {}),
    }
    url = f"{CF_API_BASE}/{spec['path']}"
    try:
        resp = session.get(url, params=params,
                           headers={"Authorization": f"Bearer {token}"},
                           timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        print(f"⚠️ [{spec['id']}] 請求失敗: {e}")
        return None

    if resp.status_code != 200:
        print(f"⚠️ [{spec['id']}] HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    try:
        payload = resp.json()
    except ValueError as e:
        print(f"⚠️ [{spec['id']}] 回應不是 JSON: {e}")
        return None
    if not payload.get("success", True):
        print(f"⚠️ [{spec['id']}] API 回報失敗: {str(payload.get('errors'))[:200]}")
        return None

    timestamps, values = parse_timeseries_payload(payload)
    if not timestamps:
        print(f"⚠️ [{spec['id']}] 回應沒有可用的時間序列")
        return None
    return {"timestamps": timestamps, "values": values}


def fetch_outage_annotations(token, days=DEFAULT_DAYS, session=None):
    """抓 Cloudflare 已知的網路中斷標註（含成因分類），作為獨立佐證。"""
    session = session or requests
    try:
        resp = session.get(
            f"{CF_API_BASE}/radar/annotations/outages",
            params={"dateRange": f"{days}d", "location": "TW", "limit": 50,
                    "format": "json"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        print(f"⚠️ 中斷標註請求失敗: {e}")
        return []
    if resp.status_code != 200:
        print(f"⚠️ 中斷標註 HTTP {resp.status_code}: {resp.text[:200]}")
        return []
    try:
        result = (resp.json() or {}).get("result") or {}
    except ValueError:
        return []
    annotations = result.get("annotations") or result.get("outages") or []
    out = []
    for a in annotations if isinstance(annotations, list) else []:
        outage = a.get("outage") or {}
        out.append({
            "start": a.get("startDate") or a.get("startdate"),
            "end": a.get("endDate") or a.get("enddate"),
            "scope": a.get("scope"),
            "event_type": a.get("eventType"),
            "cause": outage.get("outageCause") or a.get("outageCause"),
            "outage_type": outage.get("outageType") or a.get("outageType"),
            "asns": a.get("asns") or [],
            "description": a.get("description"),
            "link": a.get("linkedUrl"),
        })
    return out


# ── 異常偵測（純函式）──────────────────────────────────────────────────────

def analyze_series(spec, series):
    """對一條 Radar 序列跑偵測（薄包裝：偵測核心在 anomaly_detect）。"""
    out = analyze_values(series["timestamps"], series["values"],
                         direction=spec.get("direction", "drop"))
    out["id"] = spec["id"]
    out["label"] = spec["label"]
    return out


# ── 與海纜旁滯留船隻關聯 ────────────────────────────────────────────────────

def build_cable_index(segments, cell=CABLE_CELL_DEG):
    """把海纜線段蓋進 0.1° 格網，讓「這個點附近有沒有海纜」變成 O(1) 查詢。

    軌跡點動輒數萬個，逐點對所有海纜線段算距離太慢；先用格網篩掉 99% 的點，
    只對落在海纜格子裡的點算精確距離。
    """
    index = defaultdict(list)
    for seg in segments:
        pts = seg.get("points") or []
        for i in range(len(pts) - 1):
            (la0, lo0), (la1, lo1) = pts[i], pts[i + 1]
            # 沿線段取樣，避免長線段只蓋到兩端的格子
            steps = max(1, int(max(abs(la1 - la0), abs(lo1 - lo0)) / (cell / 2)))
            for s in range(steps + 1):
                f = s / steps
                la = la0 + (la1 - la0) * f
                lo = lo0 + (lo1 - lo0) * f
                index[(int(la / cell), int(lo / cell))].append((la0, lo0, la1, lo1))
    return index


def _near_cable_km(lat, lon, index, cell=CABLE_CELL_DEG, max_km=CORRELATE_CABLE_KM):
    """點到最近海纜的距離（km）；不在海纜格子附近回 None。"""
    ci, cj = int(lat / cell), int(lon / cell)
    best = None
    seen = set()
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for seg in index.get((ci + di, cj + dj), ()):
                if seg in seen:
                    continue
                seen.add(seg)
                d = point_to_segment_distance_km(lat, lon, *seg)
                if best is None or d < best:
                    best = d
    if best is None or best > max_km:
        return None
    return best


def _is_excluded(mmsi, name):
    """沿用 analyze_suspicious 的排除規則（浮標／漁網信標／AtoN）。

    海纜旁最密集的「低速目標」其實是漁網信標（船名帶 % 的那些），不濾掉的話
    每個異常的候選名單都會是十個浮標。共用同一份規則，日後新增規則自動生效。
    """
    try:
        from analyze_suspicious import check_exclusion_rules
    except Exception:
        return False
    excluded, _ = check_exclusion_rules(str(mmsi or ""), [name or ""])
    return excluded


def _default_port_lookup():
    """(lat, lon) -> 港口名稱／None；geofence 不可用時一律當作不在港內。"""
    try:
        from geofence import is_in_port_cached
    except Exception:
        return lambda lat, lon: None
    return is_in_port_cached


def correlate_with_vessels(events, track_entries, cable_index,
                           window_hours=CORRELATE_WINDOW_HOURS,
                           max_speed_kn=CORRELATE_MAX_SPEED_KN,
                           max_vessels=CORRELATE_MAX_VESSELS,
                           exclude=_is_excluded, port_lookup=None):
    """為每個流量異常找出「異常開始前 window_hours 內在海纜旁低速滯留」的船。

    候選依「船型威脅權重 → 距海纜距離 → 出現次數」排序：貨輪／油輪／公務船
    排在漁船前面，因為它們才有能力用錨具真正破壞海纜。

    **港內位置不算**：海纜登陸點就在港口旁邊，合法靠泊的船必然「0 節、離海纜
    很近」。這與 analyze_suspicious 的 in-port suppression 是同一個道理。

    這是關聯不是因果：AIS 可見的船只是候選名單，真正的破壞者可能關了 AIS
    （那要靠 SAR 暗船那條線）。回傳事件的淺拷貝，附上 correlated_vessels。
    """
    if port_lookup is None:
        port_lookup = _default_port_lookup()

    # tier-1 軌跡只留 14 天，但 Radar 視窗是 28 天：比軌跡更早的異常永遠比不到船。
    # 那和「比對過但沒有嫌疑船」是完全不同的結論，必須分開標記，否則報告會把
    # 「查不到」講成「沒有」。
    track_times = [t for t in (_parse_ts(e.get("timestamp")) for e in track_entries)
                   if t is not None]
    earliest_track = min(track_times) if track_times else None

    out = []
    for event in events:
        onset = _parse_ts(event.get("onset"))
        if onset is None:
            out.append({**event, "correlated_vessels": [],
                        "correlation_coverage": "unknown"})
            continue
        window_start = onset - timedelta(hours=window_hours)
        if earliest_track is None or onset < earliest_track:
            out.append({**event, "correlated_vessels": [],
                        "candidate_summary": candidate_summary([]),
                        "correlation_coverage": "outside_ais_window"})
            continue

        candidates = {}
        for entry in track_entries:
            t = _parse_ts(entry.get("timestamp"))
            if t is None or not (window_start <= t <= onset):
                continue
            for v in entry.get("vessels", []) or []:
                speed = v.get("speed")
                if speed is None or speed > max_speed_kn:
                    continue
                lat, lon = v.get("lat"), v.get("lon")
                if lat is None or lon is None:
                    continue
                dist = _near_cable_km(lat, lon, cable_index)
                if dist is None:
                    continue
                if port_lookup and port_lookup(lat, lon):
                    continue
                mmsi = str(v.get("mmsi") or "")
                rec = candidates.get(mmsi)
                if rec is None and exclude and exclude(mmsi, v.get("name")):
                    continue
                if rec is None:
                    candidates[mmsi] = {
                        "mmsi": mmsi,
                        "name": (v.get("name") or "").strip(),
                        "type": v.get("type_name"),
                        "lat": lat,
                        "lon": lon,
                        "speed": speed,
                        "nearest_cable_km": round(dist, 2),
                        "timestamp": entry.get("timestamp"),
                        "points": 1,
                    }
                    continue
                rec["points"] += 1
                # 保留「最靠近海纜」的那一筆位置作為代表
                if dist < rec["nearest_cable_km"]:
                    rec.update({
                        "name": (v.get("name") or "").strip() or rec["name"],
                        "lat": lat, "lon": lon, "speed": speed,
                        "nearest_cable_km": round(dist, 2),
                        "timestamp": entry.get("timestamp"),
                    })

        ranked = sorted(candidates.values(), key=lambda c: (
            CORRELATE_TYPE_PRIORITY.get(c.get("type"), CORRELATE_DEFAULT_PRIORITY),
            c["nearest_cable_km"],
            -c["points"],
        ))
        out.append({
            **event,
            "correlated_vessels": ranked[:max_vessels],
            "candidate_summary": candidate_summary(ranked),
            "correlation_coverage": "ok",
        })
    return out


def candidate_summary(vessels):
    """候選名單的分層計數（計入**全部**候選，不受 max_vessels 截斷影響）。

    海纜旁永遠有一堆漁船，所以「有 10 艘候選」本身沒有資訊量；真正該看的是
    有沒有商船／公務船。這個計數讓異常事件一眼看出有沒有實質線索。
    """
    counts = {"commercial": 0, "gov": 0, "other": 0}
    for v in vessels:
        pri = CORRELATE_TYPE_PRIORITY.get(v.get("type"), CORRELATE_DEFAULT_PRIORITY)
        if pri == 0:
            counts["commercial"] += 1
        elif pri == 1:
            counts["gov"] += 1
        else:
            counts["other"] += 1
    counts["total"] = len(vessels)
    return counts


def load_track_entries():
    """讀 tier-1 + tier-2 軌跡（關聯用）。讀不到回空 list。"""
    entries = []
    for name in ("ais_track_history.json", "ais_track_commercial.json"):
        path = DOCS_DIR / name
        if not path.exists():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                entries.extend(data)
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ 讀取 {name} 失敗: {e}")
    return entries


# ── 主流程 ──────────────────────────────────────────────────────────────────

def build_summary(analyzed, outages):
    all_events = [(s["id"], e) for s in analyzed for e in s["anomalies"]]
    by_sev = defaultdict(int)
    for _, e in all_events:
        by_sev[e["severity"]] += 1
    latest = max((e["onset"] for _, e in all_events), default=None)
    correlated = sum(1 for _, e in all_events if e.get("correlated_vessels"))
    # 只有商船／公務船候選才算實質線索（漁船在海纜上是常態背景）
    actionable = sum(1 for _, e in all_events
                     if (e.get("candidate_summary") or {}).get("commercial")
                     or (e.get("candidate_summary") or {}).get("gov"))
    uncorrelatable = sum(1 for _, e in all_events
                         if e.get("correlation_coverage") == "outside_ais_window")
    return {
        "series_analyzed": len(analyzed),
        "anomaly_count": len(all_events),
        "by_severity": dict(by_sev),
        "anomalies_with_vessel_candidates": correlated,
        "anomalies_with_commercial_or_gov_candidates": actionable,
        "anomalies_outside_ais_window": uncorrelatable,
        "latest_anomaly_onset": latest,
        "cloudflare_outage_annotations": len(outages),
    }


def main():
    ap = argparse.ArgumentParser(description="Cloudflare Radar 流量異常偵測")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"抓幾天的資料（預設 {DEFAULT_DAYS}；季節基線至少需 14 天）")
    ap.add_argument("--no-correlate", action="store_true",
                    help="不比對海纜旁滯留船隻")
    ap.add_argument("--retain-days", type=int, default=SERIES_RETAIN_DAYS,
                    help=f"輸出檔保留幾天的原始序列（預設 {SERIES_RETAIN_DAYS}；"
                         f"偵測仍使用完整視窗）")
    ap.add_argument("-o", "--output", default=str(DATA_DIR / "cf_radar.json"))
    args = ap.parse_args()

    token = _get_env(*TOKEN_ENV_NAMES)
    if not token:
        print("❌ 缺少 Cloudflare API token（CLOUDFLARE_API_TOKEN / "
              "CLAUDEFARETOKEN），略過")
        sys.exit(1)
    account_id = _get_env(*ACCOUNT_ENV_NAMES)
    print(f"🔑 Token 已載入（{len(token)} 字元）"
          + (f"｜Account {account_id[:6]}…（Radar 端點不需要）" if account_id else ""))
    if args.days < 14:
        print(f"⚠️ 只抓 {args.days} 天：同時段季節基線需要 ≥14 天，"
              f"會大量退回滾動中位數，偵測品質下降")

    session = requests.Session()
    analyzed = []
    for spec in SERIES_SPECS:
        print(f"📡 {spec['label']} …")
        series = fetch_series(spec, token, days=args.days, session=session)
        if series is None:
            if spec.get("optional"):
                print(f"   ↳ 略過（optional）")
            continue
        result = analyze_series(spec, series)
        n = len(result["anomalies"])
        print(f"   ↳ {result['points']} 點｜基線覆蓋 "
              f"{result['baseline_coverage']:.0%}｜異常 {n} 件"
              + (f" {[e['severity'] for e in result['anomalies']]}" if n else ""))
        analyzed.append(result)

    if not analyzed:
        print("❌ 沒有任何序列抓取成功")
        sys.exit(1)

    print("🌐 抓取 Cloudflare 已知中斷標註 …")
    outages = fetch_outage_annotations(token, days=args.days, session=session)
    print(f"   ↳ {len(outages)} 筆")

    if not args.no_correlate and any(s["anomalies"] for s in analyzed):
        print("🚢 比對海纜旁低速滯留船隻 …")
        try:
            from geofence import load_cable_segments
            cable_index = build_cable_index(load_cable_segments())
            entries = load_track_entries()
            print(f"   ↳ 海纜格網 {len(cable_index)} 格｜軌跡快照 {len(entries)} 筆")
            for s in analyzed:
                s["anomalies"] = correlate_with_vessels(
                    s["anomalies"], entries, cable_index)
            hits = sum(len(e.get("correlated_vessels", []))
                       for s in analyzed for e in s["anomalies"])
            print(f"   ↳ 候選船隻共 {hits} 艘次")
        except Exception as e:
            print(f"⚠️ 船隻關聯失敗（不影響流量偵測）: {e}")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": args.days,
        "agg_interval": AGG_INTERVAL,
        "detection": {
            "method": "hour-of-week median baseline + robust z (MAD) + "
                      "consecutive-run and relative-deviation thresholds",
            "z_threshold": Z_THRESHOLD,
            "min_consecutive": MIN_CONSECUTIVE,
            "min_deviation_pct": MIN_DEVIATION_PCT,
        },
        "series": trim_series_for_output(analyzed, retain_days=args.retain_days),
        "outage_annotations": outages,
        "summary": build_summary(analyzed, outages),
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"✅ {out}  {json.dumps(payload['summary'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
