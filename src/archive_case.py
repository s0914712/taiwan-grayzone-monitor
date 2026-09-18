#!/usr/bin/env python3
"""
================================================================================
歷史個案重建 — 從 ais-archive 分支還原單船事件
================================================================================

main 上的軌跡檔是 append-and-trim（tier-1 14 天、tier-2 28 天），
`ship_transfers.json` 也只留 14 天。一旦要回頭查一艘一個月前的船，
線上資料已經全部滾掉，**唯一**還留著原始快照的地方是 `ais-archive` 分支。

本工具把那段歷史重新組回一個「個案檔」：目標船完整航跡 + 曾經接近過的船
+ 關機區間 + 同期 SAR 暗船偵測點，供 `src/plot_encounter.py` 畫航跡圖、
或直接人工判讀。

用法：
    git fetch origin ais-archive
    python3 src/archive_case.py 620999315 --from 2026-08-13 --to 2026-08-23 \\
        -o data/cases/medna_620999315_2026-08.json

    # 指定一定要納入的伴隨船（例如已知的制裁名單命中）
    python3 src/archive_case.py 620999315 --from 2026-08-13 --to 2026-08-23 \\
        --include 610000029,610000027

注意 archive 的每一行是**該時刻全船隊的快照**（不是單船軌跡），所以要找
共位船不需要內插：同一行裡的船就是同一時刻的船。
================================================================================
"""
import argparse
import gzip
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from detect_ship_transfers import build_vessel_timelines, find_dark_gaps  # noqa: E402
from geo_utils import haversine_km  # noqa: E402
from io_utils import atomic_write_json  # noqa: E402

DEFAULT_BRANCH = "origin/ais-archive"
DEFAULT_RADIUS_KM = 10.0
DEFAULT_COMPANIONS = 6
SAR_DETECTIONS_FILE = BASE_DIR / "data" / "sar_detections.json"
# SAR 暗船偵測點納入個案的範圍（相對目標船航跡外框）
SAR_MARGIN_DEG = 0.5


def list_archive_files(branch=DEFAULT_BRANCH):
    """列出分支上 archive/ 底下的所有檔案路徑。"""
    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", branch, "--", "archive/"],
        cwd=BASE_DIR, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(
            f"❌ 讀不到分支 {branch}：{out.stderr.strip()}\n"
            f"   先執行  git fetch origin ais-archive")
    return [line for line in out.stdout.splitlines() if line.strip()]


def relevant_files(paths, date_from, date_to):
    """挑出可能涵蓋 [date_from, date_to] 的 archive 檔。

    兩種命名並存（見 CLAUDE.md）：
      * 舊的月檔   archive/ais_track_2026-08.jsonl
      * 每次執行一檔 archive/2026-08/19/ais_track_20260819T031500Z.jsonl.gz
    月檔無法只取片段，整份掃過去再用 timestamp 過濾；日檔可直接靠路徑篩掉。
    """
    # 逐月列舉，跨月的個案才不會漏掉中間月份的月檔
    months = set()
    end = date_to[:7]
    y, m = int(date_from[:4]), int(date_from[5:7])
    while f"{y:04d}-{m:02d}" <= end:
        months.add(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1

    picked = []
    for path in paths:
        parts = path.split("/")
        if len(parts) == 2:                      # archive/ais_track_2026-08.jsonl
            if any(mo in parts[1] for mo in months):
                picked.append(path)
        elif len(parts) == 4:                    # archive/2026-08/19/xxx.jsonl.gz
            day = f"{parts[1]}-{parts[2]}"
            if date_from <= day <= date_to:
                picked.append(path)
    return sorted(picked)


def iter_snapshots(path, branch=DEFAULT_BRANCH):
    """逐行 yield 一個 archive 檔裡的快照（自動處理 .gz）。

    走 `git cat-file` 的 stdout 串流，不把整份月檔（可達 100MB）讀進記憶體。
    """
    proc = subprocess.Popen(["git", "cat-file", "-p", f"{branch}:{path}"],
                            cwd=BASE_DIR, stdout=subprocess.PIPE)
    stream = gzip.GzipFile(fileobj=proc.stdout) if path.endswith(".gz") else proc.stdout
    try:
        for raw in stream:
            try:
                yield json.loads(raw)
            except ValueError:
                continue
    finally:
        if hasattr(stream, "close") and stream is not proc.stdout:
            stream.close()
        proc.stdout.close()
        proc.wait()


def _epoch(ts):
    """ISO 時戳 → epoch 秒；解析失敗回傳 0（排序時排到最前，不會誤配）。"""
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0.0


def _point(v, ts):
    return {"t": ts, "lat": v["lat"], "lon": v["lon"],
            "speed": v.get("speed"), "heading": v.get("heading"),
            "anc": v.get("anc")}


def collect_case(mmsi, date_from, date_to, radius_km=DEFAULT_RADIUS_KM,
                 branch=DEFAULT_BRANCH, include=()):
    """掃過 archive，回傳 (目標航跡, 接觸事件, 伴隨船航跡)。

    伴隨船只保留「曾經進入 radius_km」者的完整航跡 —— 全部留下會讓個案檔
    膨脹到數百 MB（一份月檔就有兩千多艘船）。

    include 的 MMSI 不受 radius_km 限制：已知有意義的船（制裁名單命中、
    同船東的姊妹船）就算全程都在 20 公里外，航跡一樣要進個案 —— 從來沒靠近過
    本身就是判讀的一部分。
    """
    files = relevant_files(list_archive_files(branch), date_from, date_to)
    if not files:
        raise SystemExit(f"❌ {date_from}~{date_to} 在 {branch} 上沒有對應的 archive 檔")
    print(f"📦 掃描 {len(files)} 個 archive 檔…")

    target = {"mmsi": mmsi, "name": "", "type_name": "", "track": []}
    encounters = []
    near = {m: {"name": "", "type_name": "unknown"} for m in include}

    # 第一趟：目標船航跡 + 誰曾經進入 radius_km。
    # 不在這趟順便收所有船的航跡 —— 一份月檔有兩千多艘船、上百個快照，
    # 全留在記憶體會膨脹到數百 MB；接觸者通常只有十幾艘，第二趟再單獨收。
    for path in files:
        for snap in iter_snapshots(path, branch):
            ts = snap.get("timestamp", "")
            if not (date_from <= ts[:10] <= date_to):
                continue
            vessels = snap.get("vessels", [])
            me = next((v for v in vessels if str(v.get("mmsi")) == mmsi), None)
            if me is None or "lat" not in me:
                continue
            target["name"] = me.get("name", "") or target["name"]
            target["type_name"] = me.get("type_name", "") or target["type_name"]
            target["track"].append(_point(me, ts))
            for v in vessels:
                m = str(v.get("mmsi"))
                if m == mmsi or "lat" not in v or "lon" not in v:
                    continue
                km = haversine_km(me["lat"], me["lon"], v["lat"], v["lon"])
                if km > radius_km:
                    continue
                near[m] = {"name": v.get("name", ""),
                           "type_name": v.get("type_name", "unknown")}
                encounters.append({
                    "t": ts, "km": round(km, 2), "mmsi": m,
                    "name": v.get("name", ""),
                    "type_name": v.get("type_name", "unknown"),
                    "speed": v.get("speed"),
                    "target_speed": me.get("speed"),
                })

    if not target["track"]:
        raise SystemExit(f"❌ {date_from}~{date_to} 的 archive 裡找不到 MMSI {mmsi}")
    target["track"].sort(key=lambda p: p["t"])
    encounters.sort(key=lambda e: e["km"])
    print(f"   目標船 {len(target['track'])} 點；{len(near)} 艘曾進入 {radius_km}km")

    # 第二趟：只收接觸者的完整航跡
    tracks = {m: [] for m in near}
    if near:
        for path in files:
            for snap in iter_snapshots(path, branch):
                ts = snap.get("timestamp", "")
                if not (date_from <= ts[:10] <= date_to):
                    continue
                for v in snap.get("vessels", []):
                    m = str(v.get("mmsi"))
                    if m in tracks and "lat" in v and "lon" in v:
                        tracks[m].append(_point(v, ts))
                        if not near[m]["name"]:
                            near[m] = {"name": v.get("name", ""),
                                       "type_name": v.get("type_name", "unknown")}

    companions = {m: {"mmsi": m, **near[m],
                      "track": sorted(pts, key=lambda p: p["t"])}
                  for m, pts in tracks.items() if pts}
    return target, encounters, companions


def closest_approach(target_track, track, max_dt_minutes=70):
    """兩條航跡的最近接觸（依最接近的時戳配對，不做內插）。

    archive 的快照是全船隊同一時刻寫入的，所以同一份快照裡的兩艘船時間必然
    對齊；`max_dt_minutes` 只是擋掉「一方在該時段根本沒有回報」的情況。
    回傳 (km, timestamp)，配不到回傳 (None, None)。
    """
    if not target_track or not track:
        return None, None
    best = (None, None)
    for p in track:
        cand = min(target_track, key=lambda q: abs(_epoch(q["t"]) - _epoch(p["t"])))
        if abs(_epoch(cand["t"]) - _epoch(p["t"])) > max_dt_minutes * 60:
            continue
        km = haversine_km(p["lat"], p["lon"], cand["lat"], cand["lon"])
        if best[0] is None or km < best[0]:
            best = (round(km, 2), p["t"])
    return best


def pick_companions(target_track, encounters, companions, limit, include=()):
    """依最近接觸距離挑出要保留完整航跡的伴隨船。

    `include` 指定的船排在最前面且不佔 limit 的名額 —— 它們之所以被指名，
    正是因為距離以外的理由（制裁名單、同船東）。
    """
    closest = {}
    for e in encounters:
        prev = closest.get(e["mmsi"])
        if prev is None or e["km"] < prev["km"]:
            closest[e["mmsi"]] = e
    ordered = sorted(closest.values(), key=lambda e: e["km"])
    chosen = [m for m in include if m in companions]
    extra = 0
    for e in ordered:
        if extra >= limit:
            break
        if e["mmsi"] in chosen:
            continue
        chosen.append(e["mmsi"])
        extra += 1
    out = []
    for m in chosen:
        c = dict(companions[m])
        km, at = closest_approach(target_track, c["track"])
        c["min_km"] = km
        c["closest_at"] = at
        c["forced"] = m in include
        out.append(c)
    out.sort(key=lambda c: (c["min_km"] is None, c["min_km"]))
    return out


def tag_markers_in_dark(markers, dark_gaps):
    """標記哪些 SAR 偵測點落在目標船的關機期間。

    GFW 的偵測只有日期沒有時刻，所以比對到「日」為止：偵測日落在任何一段
    關機區間的日期範圍內就標記。這是整份個案裡訊噪比最高的一欄 ——
    「雷達上有船、AIS 上沒有，而且正好是這艘船關機的那幾天」。
    """
    spans = [(g["start"][:10], g["end"][:10]) for g in dark_gaps]
    ends = [(g["last_lat"], g["last_lon"]) for g in dark_gaps] + \
           [(g["resume_lat"], g["resume_lon"]) for g in dark_gaps]
    for m in markers:
        m["in_dark_gap"] = any(a <= m["date"] <= b for a, b in spans)
        # 離「最後訊號 / 重新出現」兩點多遠 —— 同一天全台灣周邊都有暗船偵測，
        # 判讀時真正相關的是關機那一段海域附近的那幾筆
        m["km_from_dark"] = round(min(
            (haversine_km(m["lat"], m["lon"], la, lo) for la, lo in ends),
            default=float("inf")), 1) if ends else None
    return markers


def sar_markers_in_window(track, date_from, date_to,
                          path=SAR_DETECTIONS_FILE, margin=SAR_MARGIN_DEG):
    """同期、同海域的 GFW 未匹配暗船偵測點。

    這些點是「AIS 上不存在但雷達上看得到的船」——目標船關機期間出現在它
    漂流區裡的偵測點，可能是它自己，也可能是它的對象船；`fetch_sar_chip.py`
    量船長才分得出來。無資料時安靜略過。
    """
    if not Path(path).exists() or not track:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    lat_min = min(p["lat"] for p in track) - margin
    lat_max = max(p["lat"] for p in track) + margin
    lon_min = min(p["lon"] for p in track) - margin
    lon_max = max(p["lon"] for p in track) + margin
    out = []
    for det in data.get("dark_detections", []):
        d = det.get("date", "")
        if not (date_from <= d <= date_to):
            continue
        if lat_min <= det["lat"] <= lat_max and lon_min <= det["lon"] <= lon_max:
            out.append({"lat": det["lat"], "lon": det["lon"], "date": d,
                        "kind": "sar_dark",
                        "label": f"SAR dark {d}"})
    return out


def build_case(mmsi, date_from, date_to, radius_km=DEFAULT_RADIUS_KM,
               companions_limit=DEFAULT_COMPANIONS, include=(),
               branch=DEFAULT_BRANCH):
    target, encounters, companions = collect_case(
        mmsi, date_from, date_to, radius_km, branch, include)
    kept = pick_companions(target["track"], encounters, companions,
                           companions_limit, include)

    # 目標船的關機區間（與線上偵測同一套門檻與判讀）
    snapshots = [{"timestamp": p["t"],
                  "vessels": [{"mmsi": mmsi, "name": target["name"],
                               "lat": p["lat"], "lon": p["lon"],
                               "speed": p["speed"] or 0,
                               "type_name": target["type_name"]}]}
                 for p in target["track"]]
    dark_gaps = find_dark_gaps(build_vessel_timelines(snapshots))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": f"{branch} archive/",
        "window": {"from": date_from, "to": date_to},
        "radius_km": radius_km,
        "target": target,
        "encounters": encounters[:200],
        "companions": kept,
        "dark_gaps": dark_gaps,
        "markers": tag_markers_in_dark(
            sar_markers_in_window(target["track"], date_from, date_to), dark_gaps),
    }


def main():
    ap = argparse.ArgumentParser(description="從 ais-archive 分支重建單船個案")
    ap.add_argument("mmsi")
    ap.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    ap.add_argument("--radius-km", type=float, default=DEFAULT_RADIUS_KM)
    ap.add_argument("--companions", type=int, default=DEFAULT_COMPANIONS,
                    help="保留完整航跡的伴隨船數（依最近接觸距離）")
    ap.add_argument("--include", default="",
                    help="強制納入的伴隨船 MMSI，逗號分隔")
    ap.add_argument("--branch", default=DEFAULT_BRANCH)
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    include = [m.strip() for m in args.include.split(",") if m.strip()]
    case = build_case(args.mmsi, args.date_from, args.date_to, args.radius_km,
                      args.companions, include, args.branch)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out, case)
    t = case["target"]
    print(f"✅ 個案已輸出: {out}")
    print(f"   {t['name'] or args.mmsi}（{t['type_name']}）"
          f" {len(t['track'])} 個航跡點，"
          f"{len(case['encounters'])} 次接觸、"
          f"{len(case['companions'])} 艘伴隨船、"
          f"{len(case['dark_gaps'])} 段關機、"
          f"{len(case['markers'])} 筆 SAR 暗船點"
          f"（其中 {sum(1 for m in case['markers'] if m['in_dark_gap'])} 筆落在關機期間）")


if __name__ == "__main__":
    main()
