#!/usr/bin/env python3
"""
時間序列異常偵測 — Taiwan Gray Zone Monitor 共用模組

原本寫在 `fetch_cloudflare_radar.py` 裡，接入 IODA 的離島可達性訊號時抽出來：
判讀邏輯跟資料來源無關，兩邊都用同一套才不會各自漂移。

方法（皆為純函式）：
1. `mask_reporting_gaps` — 剛好等於 0 的點視為缺值。首次真實執行就抓到全台
   HTTP 請求量連兩小時剛好 0（-100%、z=-45.8）的假警報；整國請求量歸零在物理
   上不可能，那是回報缺口。真事件是深跌但非零，所以不會被連帶擋掉。
2. `hour_of_week_baseline` — 網路量測有很強的日／週週期，直接對原始值設門檻
   毫無意義。基線取「同一星期幾＋同一小時」其他週的中位數，且是 leave-one-out：
   異常點不會把自己的基線拉下去。樣本不足退回滾動中位數。
3. `effective_scale` — MAD×1.4826，不用標準差（一次大掉點就把 σ 撐大到之後什麼
   都偵測不到）。另設尺度下限（基線的 0.5%）：兩端都要防，MAD=0 會讓偵測直接
   放棄，MAD 過小則 1% 抖動也會變異常。
4. `detect_anomalies` — 連續 ≥2 時段**且**相對偏離 ≥10% 才成案。單點尖刺多半是
   量測雜訊；海纜中斷的特徵是持續性的位階下移，因此嚴重度由幅度×時數決定。

`trim_series_for_output` 供輸出端裁短原始陣列（這些檔每 2 小時被重寫提交一次，
完整序列會讓 repo 快速膨脹）。
"""
import statistics
from collections import defaultdict
from datetime import datetime, timezone


# 輸出裁切（見 trim_series_for_output）
SERIES_RETAIN_DAYS = 14
OUTPUT_PRECISION = 2

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


def _parse_ts(ts):
    """Radar 時間戳（'2026-07-28T00:00:00Z'）→ aware datetime。失敗回 None。"""
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)



def resample_hourly(timestamps, values):
    """把任意解析度的序列重採樣成逐時（每小時取中位數）。

    IODA 的訊號不是逐時的：bgp / merit-nt 是 5 分鐘、ping-slash24 是 10 分鐘。
    直接餵進偵測有兩個問題：
      1. `detect_anomalies` 以「一個點＝一小時」算持續時數，嚴重度會差 12 倍
      2. 連續 2 點的門檻在 5 分鐘解析度下只有 10 分鐘，太過敏感（實測澎湖的
         darknet 訊號因此一週噴出 12 件假異常）
    取中位數而非平均：低計數訊號偶爾的尖刺不該把整小時拉走。

    代價是失去小時內的解析度 —— 海纜中斷是數小時等級的事件，可以接受。
    """
    buckets = {}
    for ts, v in zip(timestamps, values):
        dt = _parse_ts(ts)
        if dt is None:
            continue
        hour = dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(hour, []).append(v)

    out_ts, out_vals = [], []
    for hour in sorted(buckets):
        vals = [v for v in buckets[hour] if v is not None]
        out_ts.append(hour.isoformat().replace("+00:00", "Z"))
        out_vals.append(statistics.median(vals) if vals else None)
    return out_ts, out_vals


def mask_reporting_gaps(values):
    """把「剛好等於 0」的點視為缺值（回傳新 list）。

    首次真實執行就抓到一筆假警報：全台灣的 HTTP 請求量連續兩小時剛好是 0，
    偏離基線 -100%、z=-45.8，被判成 high。整個國家的請求量歸零在物理上不可能，
    那是 Radar 的回報缺口。真正的斷纜是深跌但非零（同一批資料裡的 -75.9% 那筆
    才是像樣的候選），把 0 當缺值不會漏掉真事件，卻能擋掉整類假警報。
    """
    return [None if (v is not None and v == 0) else v for v in values]


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
                     min_deviation_pct=MIN_DEVIATION_PCT,
                     min_baseline_level=0.0):
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
        # 低計數守衛：基線只有 1~2 的序列上，百分比毫無意義（2→1 就是 -50%）。
        # 實測澎湖的 darknet 背景流量值域就在個位數，是假異常的主要來源。
        if abs(b) < min_baseline_level:
            continue
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



def analyze_values(timestamps, values, direction="drop", min_baseline_level=0.0,
                   resample=False):
    """對一條序列跑完整偵測流程，回傳含基線與事件的 dict（來源無關）。

    Cloudflare Radar 與 IODA 兩條線都走這裡，判讀才不會各自漂移。
    `points` 記錄偵測實際用了幾點，`reporting_gaps` 記錄遮蔽掉幾個 0 值。
    """
    if resample:
        timestamps, values = resample_hourly(timestamps, values)
    masked = mask_reporting_gaps(values)
    gaps = sum(1 for a, b in zip(values, masked) if a is not None and b is None)
    baseline = hour_of_week_baseline(timestamps, masked)
    events = detect_anomalies(timestamps, masked, baseline=baseline,
                              direction=direction,
                              min_baseline_level=min_baseline_level)
    covered = sum(1 for b in baseline if b is not None)
    return {
        "direction": direction,
        "points": len(masked),
        "reporting_gaps": gaps,
        "baseline_coverage": round(covered / len(masked), 3) if masked else 0.0,
        "timestamps": list(timestamps),
        "values": [None if v is None else round(v, 4) for v in masked],
        "baseline": [None if b is None else round(b, 4) for b in baseline],
        "anomalies": events,
    }


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


