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

CF_API_BASE = "https://api.cloudflare.com/client/v4"
REQUEST_TIMEOUT = 30

TOKEN_ENV_NAMES = ("CLOUDFLARE_API_TOKEN", "CLAUDEFARETOKEN",
                   "CLAUDEFLARETOKEN", "CF_API_TOKEN")
ACCOUNT_ENV_NAMES = ("CLOUDFLARE_ACCOUNT_ID", "CLAUDEFLAREACCOUNTID",
                     "CLAUDEFAREACCOUNTID", "CF_ACCOUNT_ID")

DEFAULT_DAYS = 28
AGG_INTERVAL = "1h"

# 偵測用整個 DEFAULT_DAYS 視窗（同時段基線需要多週樣本），但**寫進檔案的原始
# 陣列只留最近這幾天**：這個檔每 2 小時被 cron 重寫並提交一次，完整 28 天 × 6
# 條序列每次約 160KB，一年會替 repo 累積上百 MB（本專案已經被 vessel_routes
# 撐爆過一次）。前端畫圖 14 天綽綽有餘，異常事件本身很小、完整保留。
SERIES_RETAIN_DAYS = 14
OUTPUT_PRECISION = 2

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
    {
        # 連江縣（馬祖）。Radar 的行政區粒度不保證有資料，抓不到就跳過
        "id": "tw_lie_http",
        "label": "連江縣（馬祖）HTTP 請求量",
        "path": "radar/http/timeseries",
        "params": {"location": "TW-LIE"},
        "direction": "drop",
        "optional": True,
    },
    {
        "id": "tw_kin_http",
        "label": "金門縣 HTTP 請求量",
        "path": "radar/http/timeseries",
        "params": {"location": "TW-KIN"},
        "direction": "drop",
        "optional": True,
    },
]

# ── 偵測參數 ────────────────────────────────────────────────────────────────
Z_THRESHOLD = 3.0          # 穩健 z 分數門檻
MIN_CONSECUTIVE = 2        # 連續幾個時段才成案（濾掉單點雜訊）
MIN_DEVIATION_PCT = 10.0   # 相對基線的最小偏離幅度（%）
BASELINE_MIN_SAMPLES = 2   # 同時段基線至少要幾個其他週的樣本
SCALE_FLOOR_RATIO = 0.005  # z 分數尺度下限＝基線中位數的 0.5%（見 effective_scale）
FALLBACK_WINDOW = 24       # 基線退路：往前 24 個時段的滾動中位數
FALLBACK_MIN_POINTS = 6

# 嚴重度門檻
SEVERITY_CRITICAL_PCT = 30.0
SEVERITY_CRITICAL_HOURS = 3.0
SEVERITY_HIGH_PCT = 15.0
SEVERITY_HIGH_Z = 5.0

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

def _parse_ts(ts):
    """Radar 時間戳（'2026-07-28T00:00:00Z'）→ aware datetime。失敗回 None。"""
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def hour_of_week_baseline(timestamps, values, min_samples=BASELINE_MIN_SAMPLES,
                         fallback_window=FALLBACK_WINDOW):
    """每點的季節基線：同一（星期幾, 小時）其他週的中位數（leave-one-out）。

    同時段樣本不足時退回「往前 fallback_window 個時段的中位數」，兩者都不足
    則該點基線為 None（不參與偵測）。
    """
    n = len(values)
    baseline = [None] * n
    slots = defaultdict(list)
    for i, (ts, v) in enumerate(zip(timestamps, values)):
        if v is None:
            continue
        dt = _parse_ts(ts)
        if dt is None:
            continue
        slots[(dt.weekday(), dt.hour)].append((i, v))

    for items in slots.values():
        for i, _ in items:
            others = [v for j, v in items if j != i]
            if len(others) >= min_samples:
                baseline[i] = statistics.median(others)

    # 退路：滾動中位數（只看過去，避免用未來資料）
    for i in range(n):
        if baseline[i] is not None or values[i] is None:
            continue
        window = [v for v in values[max(0, i - fallback_window):i] if v is not None]
        if len(window) >= FALLBACK_MIN_POINTS:
            baseline[i] = statistics.median(window)
    return baseline


def robust_scale(residuals):
    """MAD×1.4826（≈ 穩健標準差）。全為 0 或樣本太少時回 None。"""
    vals = [r for r in residuals if r is not None]
    if len(vals) < 4:
        return None
    med = statistics.median(vals)
    mad = statistics.median([abs(r - med) for r in vals])
    scale = mad * 1.4826
    return scale if scale > 0 else None


def effective_scale(residuals, baseline, floor_ratio=SCALE_FLOOR_RATIO):
    """實際用於 z 分數的尺度：MAD 尺度與「基線的 floor_ratio」取大者。

    兩個極端都要處理：
    - 序列極度規律時 MAD 會是 0（合成資料、或流量非常平穩的 ASN），
      純 MAD 會回 None 而讓偵測直接放棄——但持續掉 45% 顯然是異常。
    - 反過來，MAD 極小時 z 會爆到幾百，任何 1% 的抖動都變「異常」。
    以基線的一個小比例當下限，兩個問題一起解決。
    """
    mad_scale = robust_scale(residuals)
    bases = [abs(b) for b in baseline if b]
    floor = statistics.median(bases) * floor_ratio if bases else 0.0
    candidates = [s for s in (mad_scale, floor) if s and s > 0]
    return max(candidates) if candidates else None


def detect_anomalies(timestamps, values, baseline=None, direction="drop",
                     z_threshold=Z_THRESHOLD, min_consecutive=MIN_CONSECUTIVE,
                     min_deviation_pct=MIN_DEVIATION_PCT):
    """偵測相對季節基線的持續性偏離。

    direction='drop' 抓下掉（流量），'spike' 抓上衝（延遲）。
    回傳事件清單：onset / end / duration_hours / points / peak_z /
    max_deviation_pct / mean_deviation_pct / severity。
    """
    if baseline is None:
        baseline = hour_of_week_baseline(timestamps, values)

    residuals = [None if (v is None or b is None) else v - b
                 for v, b in zip(values, baseline)]
    scale = effective_scale(residuals, baseline)
    if scale is None:
        return []

    sign = -1.0 if direction == "drop" else 1.0
    flagged = []
    for i, (v, b, r) in enumerate(zip(values, baseline, residuals)):
        if r is None or not b:
            continue
        z = r / scale
        dev_pct = (r / b) * 100.0
        # 方向要對、z 要夠大、相對幅度也要夠大（避免小基數的假警報）
        if sign * z >= z_threshold and sign * dev_pct >= min_deviation_pct:
            flagged.append({"index": i, "timestamp": timestamps[i], "value": v,
                            "baseline": b, "z": round(z, 2),
                            "deviation_pct": round(dev_pct, 1)})

    events = []
    run = []
    for item in flagged:
        if run and item["index"] != run[-1]["index"] + 1:
            events.append(run)
            run = []
        run.append(item)
    if run:
        events.append(run)

    out = []
    for run in events:
        if len(run) < min_consecutive:
            continue
        devs = [p["deviation_pct"] for p in run]
        zs = [p["z"] for p in run]
        duration = float(len(run))  # aggInterval 為 1h
        event = {
            "onset": run[0]["timestamp"],
            "end": run[-1]["timestamp"],
            "duration_hours": duration,
            "points": len(run),
            "direction": direction,
            "peak_z": min(zs) if direction == "drop" else max(zs),
            "max_deviation_pct": min(devs) if direction == "drop" else max(devs),
            "mean_deviation_pct": round(sum(devs) / len(devs), 1),
            "baseline_at_onset": round(run[0]["baseline"], 4),
            "value_at_onset": round(run[0]["value"], 4),
        }
        event["severity"] = classify_severity(event)
        out.append(event)
    return out


def classify_severity(event):
    """依偏離幅度 × 持續時數分級。海纜中斷的特徵是幅度大且持續。"""
    dev = abs(event.get("max_deviation_pct") or 0)
    hours = event.get("duration_hours") or 0
    peak_z = abs(event.get("peak_z") or 0)
    if dev >= SEVERITY_CRITICAL_PCT and hours >= SEVERITY_CRITICAL_HOURS:
        return "critical"
    if dev >= SEVERITY_HIGH_PCT or peak_z >= SEVERITY_HIGH_Z:
        return "high"
    return "medium"


def analyze_series(spec, series):
    """對一條序列跑偵測，回傳含基線與事件的結果 dict。"""
    timestamps = series["timestamps"]
    values = series["values"]
    baseline = hour_of_week_baseline(timestamps, values)
    events = detect_anomalies(timestamps, values, baseline=baseline,
                              direction=spec.get("direction", "drop"))
    covered = sum(1 for b in baseline if b is not None)
    return {
        "id": spec["id"],
        "label": spec["label"],
        "direction": spec.get("direction", "drop"),
        "points": len(values),
        "baseline_coverage": round(covered / len(values), 3) if values else 0.0,
        "timestamps": timestamps,
        "values": [None if v is None else round(v, 4) for v in values],
        "baseline": [None if b is None else round(b, 4) for b in baseline],
        "anomalies": events,
    }


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
    out = []
    for event in events:
        onset = _parse_ts(event.get("onset"))
        if onset is None:
            out.append({**event, "correlated_vessels": []})
            continue
        window_start = onset - timedelta(hours=window_hours)

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

def trim_series_for_output(series, retain_days=SERIES_RETAIN_DAYS,
                           precision=OUTPUT_PRECISION):
    """只保留最近 retain_days 的原始陣列並降低小數位（純函式，回傳新 dict）。

    偵測結果（anomalies）與 metadata 完全不動——被裁掉的只是圖表用不到的舊點。
    `points` 仍記錄偵測時實際用了幾點，避免看檔案的人以為偵測只吃了 14 天。
    """
    keep = int(retain_days * 24)

    def _round(v):
        return None if v is None else round(v, precision)

    out = []
    for s in series:
        ts = s.get("timestamps") or []
        trimmed = dict(s)
        trimmed["series_retained_days"] = retain_days
        trimmed["timestamps"] = ts[-keep:]
        for key in ("values", "baseline"):
            arr = s.get(key) or []
            trimmed[key] = [_round(v) for v in arr[-keep:]]
        out.append(trimmed)
    return out


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
    return {
        "series_analyzed": len(analyzed),
        "anomaly_count": len(all_events),
        "by_severity": dict(by_sev),
        "anomalies_with_vessel_candidates": correlated,
        "anomalies_with_commercial_or_gov_candidates": actionable,
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
