#!/usr/bin/env python3
"""
昨日中國公務船（海警／海巡／海救／科研·情報）單日動態彙整 — Taiwan Gray Zone Monitor

從 tier-1 航跡檔（docs/ais_track_history.json）切出「某一個台灣時間日曆日」的公務船
位置點，彙整成逐船動態（出現時段、位置、速度、日內移動距離、最近距台灣基線的法域），
供 LINE 每日簡報（src/SendMessage.py）產文與繪圖使用。

本模組的彙整函式皆為純函式（吃 entries、吐 dict），法域標註與繪圖另外分離，
方便在沒有基線檔／matplotlib 的環境下測試。

CLI（除錯用）：
    python3 src/gov_daily_activity.py            # 印出昨日動態摘要
    python3 src/gov_daily_activity.py --days-back 2 -o out.png
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from geo_utils import haversine_km  # noqa: E402

TW_TZ = timezone(timedelta(hours=8))

TIER1_TRACK_FILE = DOCS_DIR / "ais_track_history.json"

# 公務船子類別（與 fetch_ais_data.classify_gov_vessel 一致）
CATEGORY_LABEL = {
    "coastguard": "海警",
    "msa": "海巡（海事局）",
    "rescue": "海救（救助局）",
    "research": "科研／情報船",
}
# 併排列舉時用的短標籤（避免「海巡（海事局） 3 艘」括號套括號）
CATEGORY_SHORT = {
    "coastguard": "海警",
    "msa": "海巡",
    "rescue": "海救",
    "research": "科研／情報",
}
CATEGORY_ORDER = ["coastguard", "msa", "rescue", "research"]
# 地圖標題用（英文，避免 runner 缺 CJK 字型時整行變豆腐方塊）
CATEGORY_TITLE_EN = {
    "coastguard": "Coast Guard",
    "msa": "MSA Patrol",
    "rescue": "Rescue & Salvage",
    "research": "Research / Intel",
}

# 主角類別：報告詳細點名的類別（其餘只報艘數）
PRIMARY_CATEGORY = "coastguard"
# 詳細點名的最多艘數（LINE 訊息長度有限）
MAX_DETAIL_VESSELS = 6

ZONE_LABEL = {
    "internal_waters": "台灣內水",
    "territorial_sea": "台灣領海（12浬內）",
    "contiguous_zone": "鄰接區（12–24浬）",
    "eez": "專屬經濟海域 EEZ",
    "high_seas": "公海／他國海域",
    "unknown": "法域不明",
}

# MMSI 開頭 9 為助航設備／岸台，不是船（與 analyze_suspicious 的排除規則一致）
_ATON_MMSI_PREFIX = "9"


def tw_day_window(now=None, days_back=1):
    """回傳某個台灣時間日曆日的 [起, 迄) 邊界與日期字串。

    days_back=1 → 昨日；0 → 今日（尚未結束）。
    """
    now = now or datetime.now(TW_TZ)
    now = now.astimezone(TW_TZ)
    day = (now - timedelta(days=days_back)).date()
    start = datetime(day.year, day.month, day.day, tzinfo=TW_TZ)
    return start, start + timedelta(days=1), day.strftime("%Y/%m/%d")


def load_tier1_entries(path=None):
    """讀取 tier-1 航跡檔（list of snapshot entries）。讀不到回空 list。"""
    path = Path(path) if path else TIER1_TRACK_FILE
    if not path.exists():
        print(f"⚠️ 找不到航跡檔 {path}")
        return []
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠️ 讀取 {path} 失敗: {e}")
        return []
    return entries if isinstance(entries, list) else []


def _entry_time(entry):
    ts = entry.get("timestamp")
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t


def _vessel_category(vessel, classifier=None):
    """判定公務船子類別。優先用 tier-1 的 gov 旗標，再退回 type_name / 船名關鍵字。"""
    cat = vessel.get("gov")
    if cat in CATEGORY_LABEL:
        return cat
    cat = vessel.get("type_name")
    if cat in CATEGORY_LABEL:
        return cat
    if classifier:
        cat = classifier(vessel.get("name", ""))
        if cat in CATEGORY_LABEL:
            return cat
    return None


def _default_classifier():
    try:
        from fetch_ais_data import classify_gov_vessel
    except Exception:
        return None
    return lambda name: classify_gov_vessel(name)


def _default_port_lookup():
    """回傳 (lat, lon) -> 港口名稱／None 的查詢函式；geofence 不可用時一律當作海上。"""
    try:
        from geofence import is_in_port_cached
    except Exception:
        return lambda lat, lon: None
    return is_in_port_cached


def collect_daily_gov_activity(entries, start, end, classifier=None,
                               port_lookup=None):
    """切出 [start, end) 期間內的公務船動態，回傳逐船 dict 清單（純函式）。

    每筆：mmsi / name / category / points / point_count / first_seen / last_seen
          / last_lat / last_lon / max_speed / avg_speed / distance_km / moving
          / at_sea / in_port_points / port_name
    points 內每點 {t(iso), lat, lon, speed}，依時間排序。

    **靠港位置不列入動態**：停在廈門、福州錨地不是海上活動，報告與地圖都不該
    出現。每個位置點以 `port_lookup`（預設 geofence.is_in_port_cached，台灣港口
    2km／大陸港灣 8km+）判定，`points` 只留海上點；整天都在港內的船 `at_sea`
    為 False，由 `summarize_activity` / `build_daily_gov_map` 濾掉（仍保留在
    回傳值裡，供「另有 N 艘在港內」這類說明使用）。

    依「類別順序 → 點數多寡」排序，方便直接取前 N 艘點名。
    """
    if classifier is None:
        classifier = _default_classifier()
    if port_lookup is None:
        port_lookup = _default_port_lookup()

    by_mmsi = {}
    for entry in entries:
        t = _entry_time(entry)
        if t is None or not (start <= t.astimezone(start.tzinfo) < end):
            continue
        for v in entry.get("vessels", []) or []:
            mmsi = str(v.get("mmsi") or "")
            if not mmsi or mmsi.startswith(_ATON_MMSI_PREFIX):
                continue
            category = _vessel_category(v, classifier)
            if not category:
                continue
            lat, lon = v.get("lat"), v.get("lon")
            if lat is None or lon is None:
                continue
            rec = by_mmsi.setdefault(mmsi, {
                "mmsi": mmsi,
                "category": category,
                "names": [],
                "points": [],
            })
            name = (v.get("name") or "").strip()
            if name:
                rec["names"].append(name)
            rec["points"].append({
                "t": t.astimezone(start.tzinfo).isoformat(),
                "lat": lat,
                "lon": lon,
                "speed": v.get("speed") or 0.0,
            })

    activity = []
    for rec in by_mmsi.values():
        all_pts = sorted(rec["points"], key=lambda p: p["t"])
        ports = [port_lookup(p["lat"], p["lon"]) for p in all_pts]
        sea_pts = [p for p, port in zip(all_pts, ports) if not port]
        in_port_count = len(all_pts) - len(sea_pts)
        at_sea = bool(sea_pts)
        # 有出海就只用海上點算動態（時段／速度／移動距離都是「在海上」的行為）；
        # 整天沒離港的船保留原始點，但標記 at_sea=False 供上層濾除
        pts = sea_pts if at_sea else all_pts
        speeds = [p["speed"] for p in pts]
        dist = float(sum(
            haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
            for a, b in zip(pts, pts[1:])
        ))
        activity.append({
            "mmsi": rec["mmsi"],
            "category": rec["category"],
            # 同一 MMSI 可能有多種寫法（HAIXUN07602 / HAIXUN 07602）→ 取最常見者
            "name": max(set(rec["names"]), key=rec["names"].count) if rec["names"] else f"MMSI-{rec['mmsi']}",
            "points": pts,
            "point_count": len(pts),
            "first_seen": pts[0]["t"],
            "last_seen": pts[-1]["t"],
            "last_lat": pts[-1]["lat"],
            "last_lon": pts[-1]["lon"],
            "max_speed": round(max(speeds), 1) if speeds else 0.0,
            "avg_speed": round(sum(speeds) / len(speeds), 1) if speeds else 0.0,
            "distance_km": round(dist, 1),
            # 只有一筆位置時談不上移動與否（距離必為 0），另外標記避免誤導
            "moving": dist >= 5.0 and len(pts) >= 2,
            "at_sea": at_sea,
            "in_port_points": in_port_count,
            "port_name": next((p for p in ports if p), None),
        })

    order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    activity.sort(key=lambda a: (order.get(a["category"], 9), -a["point_count"]))
    return activity


def at_sea_only(activity):
    """只留下當天有離開港口的船 —— 靠港停泊不算海上活動。"""
    return [a for a in activity if a.get("at_sea", True)]


def category_counts(activity):
    """各子類別艘數（dict，僅含出現的類別）。"""
    counts = {}
    for a in activity:
        counts[a["category"]] = counts.get(a["category"], 0) + 1
    return counts


def annotate_zones(activity):
    """為每艘船標註「最靠近台灣的那個點」的法域與距基線浬數（需要基線檔）。

    寫入 closest_zone / closest_nm / closest_lat / closest_lon。
    基線檔或 geofence 不可用時靜默跳過（欄位維持 None）。
    """
    try:
        from geofence import classify_maritime_zone, load_baselines
        baselines = load_baselines()
    except Exception as e:
        print(f"⚠️ 無法載入海域基線，略過法域標註: {e}")
        baselines = None

    for a in activity:
        a.setdefault("closest_zone", None)
        a.setdefault("closest_nm", None)
        # 整天沒離港的船不會進報告，省下逐點的基線距離計算
        if not baselines or not a.get("at_sea", True):
            continue
        best = None
        for p in a["points"]:
            z = classify_maritime_zone(p["lat"], p["lon"], baselines=baselines)
            nm = z.get("distance_to_baseline_nm")
            if nm is None:
                continue
            if best is None or nm < best[0]:
                best = (nm, z.get("zone", "unknown"), p)
        if best:
            nm, zone, p = best
            a["closest_zone"] = zone
            a["closest_nm"] = round(nm, 1)
            a["closest_lat"] = p["lat"]
            a["closest_lon"] = p["lon"]
    return activity


def _hhmm(iso_ts):
    try:
        return datetime.fromisoformat(iso_ts).astimezone(TW_TZ).strftime("%H:%M")
    except (ValueError, TypeError):
        return "?"


def _vessel_activity_line(a):
    """單艘公務船的單行動態描述（給模板／LLM context 用）。"""
    if a["point_count"] < 2:
        # 單筆位置：距離必為 0，講「幾乎定點」會誤導
        movement = "｜僅 1 筆位置，無法判斷移動"
    else:
        movement = (f"｜日內移動約 {a['distance_km']} 公里"
                    + ("" if a["moving"] else "（幾乎定點）"))
    parts = [
        f"- {a['name']}（MMSI {a['mmsi']}）",
        f"  出現 {_hhmm(a['first_seen'])}–{_hhmm(a['last_seen'])}（{a['point_count']} 筆位置）",
        f"  最新位置 {a['last_lat']:.2f}N,{a['last_lon']:.2f}E｜最高速 {a['max_speed']} 節"
        + movement,
    ]
    if a.get("closest_nm") is not None:
        zone = ZONE_LABEL.get(a.get("closest_zone"), a.get("closest_zone") or "?")
        parts.append(f"  最近距台灣基線 {a['closest_nm']} 浬（{zone}）")
    return "\n".join(parts)


def summarize_activity(activity, day_label, primary=PRIMARY_CATEGORY,
                       max_detail=MAX_DETAIL_VESSELS, note_in_port=False):
    """把單日公務船動態彙整成一段中文文字（LLM context + 模板共用）。

    只涵蓋**在海上**的船（靠港停泊不是活動）；主角類別（預設海警）逐船點名，
    其餘類別只報艘數。完全沒有海上活動時回傳一句「未偵測到」的說明，讓報告
    不會空白。

    note_in_port: 是否附註「另有 N 艘整日停泊港內」。推播報告不需要（在港的事
    不必報告），CLI 除錯時打開比較看得出資料被濾掉多少。
    """
    sea = at_sea_only(activity)
    in_port_total = len(activity) - len(sea)
    counts = category_counts(sea)
    primary_list = [a for a in sea if a["category"] == primary][:max_detail]
    primary_total = counts.get(primary, 0)
    primary_label = CATEGORY_LABEL.get(primary, primary)

    lines = [f"昨日（{day_label}，台灣時間）中國公務船海上動態（不含靠港停泊）："]
    if primary_total:
        lines.append(f"{primary_label}船共 {primary_total} 艘在海上活動：")
        lines.extend(_vessel_activity_line(a) for a in primary_list)
        if primary_total > len(primary_list):
            lines.append(f"（另有 {primary_total - len(primary_list)} 艘{primary_label}船未列出）")
    else:
        lines.append(f"昨日未偵測到中國{primary_label}船在海上活動。")

    others = [f"{CATEGORY_SHORT[c]} {counts[c]} 艘"
              for c in CATEGORY_ORDER if c != primary and counts.get(c)]
    if others:
        lines.append("其他中國公務／關注船（海上）：" + "、".join(others))
    if note_in_port and in_port_total:
        lines.append(f"（另有 {in_port_total} 艘整日停泊港內，不列入動態）")
    return "\n".join(lines)


def build_daily_gov_map(activity, output_path, day_label,
                        primary=PRIMARY_CATEGORY):
    """繪製單日公務船航跡圖（只畫在海上的船，靠港停泊不入圖）。

    畫出當天所有在海上的公務船並依子類別著色；海警排在最前面，因此船名標註
    優先落在海警身上（有些日子只有一兩艘海警在海上，只畫海警會是一張空圖，
    連帶看不出其他公務船的分布）。
    回傳輸出路徑，無資料或 matplotlib 不可用時回傳 None。
    """
    try:
        from plot_gov_vessel_tracks import TAIWAN_VIEW_BOUNDS, plot_tracks
    except Exception as e:
        print(f"⚠️ 無法載入繪圖模組，略過公務船動態圖: {e}")
        return None

    sea = at_sea_only(activity)
    if not sea:
        print("⚠️ 昨日無公務船在海上活動，略過動態圖")
        return None

    vessels = [{
        "name": a["name"],
        "mmsi": a["mmsi"],
        "category": a["category"],
        "track": a["points"],
    } for a in sea]

    # 標題用英文（圖例已標中文）：runner 少了 CJK 字型時整行標題不會變豆腐方塊
    primary_count = sum(1 for a in sea if a["category"] == primary)
    title = (f"China Gov Vessels at Sea {day_label}  "
             f"({len(vessels)} vessels, {CATEGORY_TITLE_EN.get(primary, primary)} "
             f"{primary_count})")
    # 海警是主角，全部標名；陪襯類別少標幾艘，免得福建沿岸擠成一團字
    label_quota = {c: (6 if c == primary else 2) for c in CATEGORY_ORDER}
    return plot_tracks(vessels, output_path, title=title, max_labels=label_quota,
                       pad=0.8, include_bounds=TAIWAN_VIEW_BOUNDS)


def main():
    ap = argparse.ArgumentParser(description="彙整昨日中國公務船動態（除錯用）")
    ap.add_argument("--days-back", type=int, default=1,
                    help="往回推幾天（1=昨日，預設 1）")
    ap.add_argument("-o", "--output", help="同時輸出航跡圖 PNG 路徑")
    args = ap.parse_args()

    start, end, day_label = tw_day_window(days_back=args.days_back)
    entries = load_tier1_entries()
    activity = annotate_zones(collect_daily_gov_activity(entries, start, end))
    sea = at_sea_only(activity)
    print(f"🔎 {day_label} 公務船 {len(activity)} 艘（海上 {len(sea)}／"
          f"整日在港 {len(activity) - len(sea)}）{category_counts(sea)}")
    print("─" * 40)
    print(summarize_activity(activity, day_label, note_in_port=True))
    print("─" * 40)
    if args.output:
        build_daily_gov_map(activity, args.output, day_label)


if __name__ == "__main__":
    main()
