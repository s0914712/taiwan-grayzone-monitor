#!/usr/bin/env python3
"""
Threads 社群媒體自動發布腳本 — Taiwan Gray Zone Monitor
從 data.json 讀取最新監測數據，產生摘要圖表與可疑船隻航跡圖，
並以 Carousel 格式發布到 Threads。

環境變數:
  THREADS_USER_ID       — Threads 用戶 ID
  THREADS_ACCESS_TOKEN  — Threads API 存取權杖
  THREADS_APP_SECRET    — Threads App Secret
  GITHUB_TOKEN          — GitHub API token（圖片上傳用）
  GEMINI_API_KEY        — Google Gemini API key（LLM 產生貼文用，選填）

Usage: python publish_threads.py [--dry-run] [--mode daily|weekly]
"""
import argparse
import base64
import hashlib
import hmac
import json
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
DATA_DIR = BASE_DIR / "data"
SRC_DIR = BASE_DIR / "src"

REPO_OWNER = "s0914712"
REPO_NAME = "taiwan-grayzone-monitor"
CHART_DIR = "data/charts"
CHART_FILENAME = "threads_summary.png"
CHART_REPO_PATH = f"{CHART_DIR}/{CHART_FILENAME}"

TW_TZ = timezone(timedelta(hours=8))

# ── 台灣本島簡化輪廓座標 (lat, lon) ─────────────────────────
TAIWAN_COASTLINE = [
    (25.29, 121.57), (25.17, 121.74), (25.03, 121.96),
    (24.98, 121.98), (24.83, 121.84), (24.59, 121.60),
    (24.32, 121.51), (24.08, 121.59), (23.76, 121.48),
    (23.47, 121.35), (23.09, 121.17), (22.76, 121.07),
    (22.52, 120.75), (22.37, 120.59), (22.00, 120.70),
    (22.35, 120.30), (22.59, 120.27), (22.92, 120.26),
    (23.28, 120.18), (23.56, 120.21), (23.93, 120.30),
    (24.25, 120.47), (24.64, 120.68), (24.84, 120.85),
    (25.10, 121.25), (25.29, 121.57),
]

MIN_TRACK_POINTS = 15


def _haversine_km(lat1, lon1, lat2, lon2):
    """兩點間距離（公里）"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _point_near_any_cable(lat, lon, cable_segments, threshold_km=5.0):
    """Check if a point is within threshold_km of any cable segment."""
    for cable in cable_segments:
        pts = cable['points']
        for i in range(len(pts) - 1):
            # Simple point-to-segment check using projection
            lat1, lon1 = pts[i]
            lat2, lon2 = pts[i + 1]
            dx, dy = lat2 - lat1, lon2 - lon1
            if dx == 0 and dy == 0:
                dist = _haversine_km(lat, lon, lat1, lon1)
            else:
                t = max(0, min(1, ((lat - lat1) * dx + (lon - lon1) * dy) / (dx * dx + dy * dy)))
                dist = _haversine_km(lat, lon, lat1 + t * dx, lon1 + t * dy)
            if dist < threshold_km:
                return True
    return False


def _load_cable_segments():
    """載入海纜 GeoJSON，提取台灣周邊的線段座標"""
    cable_file = DATA_DIR / "cable-geo.json"
    if not cable_file.exists():
        return []

    with open(cable_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    segments = []
    for feat in data.get('features', []):
        slug = feat.get('properties', {}).get('slug', '')
        coords = feat.get('geometry', {}).get('coordinates', [])
        for segment in coords:
            tw_points = []
            for lon, lat in segment:
                if 19 <= lat <= 28 and 115 <= lon <= 130:
                    tw_points.append((lat, lon))
            if len(tw_points) >= 2:
                segments.append({'slug': slug, 'points': tw_points})
    return segments


CARGO_TYPES = {'cargo', 'tanker', 'lng'}


def select_top_suspicious_vessels(n=2):
    """Select top N vessels for visualization.

    Priority: cargo/tanker/lng vessels with highest cable loitering hours.
    Fallback: any suspicious vessel with highest loitering hours.
    """
    susp_file = DATA_DIR / "suspicious_vessels.json"
    if not susp_file.exists():
        print("⚠️ suspicious_vessels.json not found")
        return []

    with open(susp_file, encoding="utf-8") as f:
        data = json.load(f)

    vessels = data.get("suspicious_vessels", [])
    if not vessels:
        return []

    def _loiter_hours(v):
        return v.get("cable_details", {}).get("loiter_slow_hours", 0)

    def _with_track(v):
        route_file = DOCS_DIR / "vessel_routes" / f"{v['mmsi']}.json"
        if not route_file.exists():
            return None
        try:
            with open(route_file, encoding="utf-8") as rf:
                route = json.load(rf)
            track = route.get("track", [])
            if len(track) < MIN_TRACK_POINTS:
                return None
            return track
        except (json.JSONDecodeError, IOError):
            return None

    # Prefer cargo/tanker/lng vessels with cable loitering, sorted by loiter hours
    cargo_loiterers = []
    for v in vessels:
        if v.get("vessel_type") not in CARGO_TYPES:
            continue
        if not v.get("cable_loitering"):
            continue
        track = _with_track(v)
        if track is None:
            continue
        cargo_loiterers.append((_loiter_hours(v), v, track))

    cargo_loiterers.sort(key=lambda x: -x[0])
    if cargo_loiterers:
        return [{**v, "_track": t} for _, v, t in cargo_loiterers[:n]]

    # Fallback: any vessel with highest loitering hours
    fallback = []
    for v in vessels:
        track = _with_track(v)
        if track is None:
            continue
        fallback.append((_loiter_hours(v), v, track))

    fallback.sort(key=lambda x: -x[0])
    return [{**v, "_track": t} for _, v, t in fallback[:n]]


def generate_track_map(vessel, output_path):
    """Generate a dark-themed vessel track map image with cable routes."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.collections import LineCollection
    except ImportError:
        print("⚠️ matplotlib not available, skipping track map generation")
        return None

    track = vessel.get("_track", [])
    if len(track) < MIN_TRACK_POINTS:
        return None

    cable_segments = _load_cable_segments()

    lats = [p["lat"] for p in track]
    lons = [p["lon"] for p in track]
    pad = 0.3
    lat_min, lat_max = min(lats) - pad, max(lats) + pad
    lon_min, lon_max = min(lons) - pad, max(lons) + pad

    fig, ax = plt.subplots(figsize=(8, 8))
    fig.patch.set_facecolor('#0a1628')
    ax.set_facecolor('#0a1628')

    # Taiwan coastline
    tw_lats = [p[0] for p in TAIWAN_COASTLINE]
    tw_lons = [p[1] for p in TAIWAN_COASTLINE]
    ax.fill(tw_lons, tw_lats, facecolor='#1a2640', edgecolor='#2a3a5a', linewidth=1, zorder=1)

    # Cable routes within bounding box
    for cable in cable_segments:
        pts = cable['points']
        clats = [p[0] for p in pts]
        clons = [p[1] for p in pts]
        # Filter to visible area
        visible = any(lat_min - 0.5 <= la <= lat_max + 0.5 and
                       lon_min - 0.5 <= lo <= lon_max + 0.5
                       for la, lo in zip(clats, clons))
        if visible:
            ax.plot(clons, clats, color='#00f5ff', alpha=0.25, linewidth=0.8,
                    linestyle='--', zorder=2)

    # Vessel track with per-segment coloring
    segments = []
    colors = []
    for i in range(len(track) - 1):
        p1, p2 = track[i], track[i + 1]
        segments.append([(p1["lon"], p1["lat"]), (p2["lon"], p2["lat"])])

        heading_change = abs(p2.get("heading", 0) - p1.get("heading", 0))
        if heading_change > 180:
            heading_change = 360 - heading_change
        is_slow = p1.get("speed", 99) < 8.0
        is_near_cable = _point_near_any_cable(p1["lat"], p1["lon"], cable_segments)
        is_suspicious = (is_slow and is_near_cable) or heading_change > 45
        colors.append('#ff3366' if is_suspicious else '#00ff88')

    lc = LineCollection(segments, colors=colors, linewidths=1.8, zorder=3)
    ax.add_collection(lc)

    # Start / end markers
    ax.plot(track[0]["lon"], track[0]["lat"], 'o', color='#00ff88',
            markersize=8, zorder=4, markeredgecolor='white', markeredgewidth=0.5)
    ax.plot(track[-1]["lon"], track[-1]["lat"], 's', color='#ff3366',
            markersize=8, zorder=4, markeredgecolor='white', markeredgewidth=0.5)

    # Annotation box
    name_raw = vessel.get("names", ["Unknown"])[0]
    name = name_raw.split("--")[0]  # Strip AIS confidence suffix
    mmsi = vessel.get("mmsi", "?")
    cable_det = vessel.get("cable_details", {})
    zigzag_det = vessel.get("zigzag_details", {})
    loiter_h = round(cable_det.get("loiter_slow_hours", 0))
    turns = zigzag_det.get("turn_count", 0)
    n_names = len(vessel.get("names", []))

    info_lines = [
        f"{name}  (MMSI: {mmsi})",
        f"Risk: {vessel.get('risk_level', '?').upper()} ({vessel.get('risk_score', '?')}/15)",
        f"Turns: {turns} | Cable loiter: {loiter_h}h | Names: {n_names}",
    ]
    if track:
        t_start = track[0].get("t", "")[:10]
        t_end = track[-1].get("t", "")[:10]
        info_lines.append(f"Track: {t_start} → {t_end}  ({len(track)} pts)")

    info_text = "\n".join(info_lines)
    ax.text(0.02, 0.98, info_text, transform=ax.transAxes, fontsize=8,
            color='#e8eef7', verticalalignment='top',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#141e32', alpha=0.9,
                      edgecolor='#2a3a5a'),
            zorder=5)

    # Legend
    ax.plot([], [], color='#ff3366', linewidth=2, label='Suspicious (slow near cable / zigzag)')
    ax.plot([], [], color='#00ff88', linewidth=2, label='Normal transit')
    ax.plot([], [], '--', color='#00f5ff', alpha=0.5, linewidth=1, label='Submarine cables')
    ax.legend(loc='lower right', fontsize=7, facecolor='#141e32', edgecolor='#2a3a5a',
              labelcolor='#8aa4c8')

    # Grid
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.tick_params(colors='#2a3a5a', labelsize=7)
    ax.grid(True, color='#1a2a40', linewidth=0.5, alpha=0.5)
    for spine in ax.spines.values():
        spine.set_color('#2a3a5a')

    ax.set_aspect('equal')
    ax.set_xlabel('Longitude', color='#445566', fontsize=8)
    ax.set_ylabel('Latitude', color='#445566', fontsize=8)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=200, bbox_inches='tight', facecolor='#0a1628')
    plt.close(fig)
    print(f"✅ Track map saved: {output_path}")
    return output_path


def generate_summary(mode="daily"):
    """Run generate_summary.py and return the summary dict."""
    # Import the summary module directly
    sys.path.insert(0, str(SRC_DIR))
    from generate_summary import load_data, compute_daily_summary, compute_weekly_summary

    data = load_data()
    if mode == "weekly":
        return compute_weekly_summary(data), data
    return compute_daily_summary(data), data


def generate_chart(summary, output_path):
    """Generate a simple summary chart image for the Threads post."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        print("⚠️ matplotlib not available, skipping chart generation")
        return None

    fig, ax = plt.subplots(figsize=(8, 4.5))
    fig.patch.set_facecolor('#0a1628')
    ax.set_facecolor('#0a1628')

    date_str = datetime.now(TW_TZ).strftime("%Y/%m/%d")
    is_weekly = summary.get("type") == "weekly"
    title = f"Taiwan Gray Zone {'Weekly' if is_weekly else 'Daily'} Report — {date_str}"

    ax.text(0.5, 0.92, title, transform=ax.transAxes,
            fontsize=16, fontweight='bold', color='#00f5ff',
            ha='center', va='top')

    # Key metrics as large text blocks
    metrics = [
        ("AIS Vessels", f"{summary.get('ais_total', 0):,}", "#00f5ff"),
        ("Dark Vessels", f"{summary.get('dark_vessels_total', 0)}", "#ff6b6b"),
        ("Suspicious", f"{summary.get('suspicious_count', 0)}", "#ffd93d"),
        ("LNG/Gas", f"{summary.get('lng_vessels', 0)}", "#f0e130"),
        ("ID Changes", f"{summary.get('identity_changes_24h', 0)}", "#ff8800"),
    ]

    n = len(metrics)
    for i, (label, value, color) in enumerate(metrics):
        x = (i + 0.5) / n
        # Value
        ax.text(x, 0.55, value, transform=ax.transAxes,
                fontsize=28, fontweight='bold', color=color,
                ha='center', va='center')
        # Label
        ax.text(x, 0.35, label, transform=ax.transAxes,
                fontsize=10, color='#8899aa',
                ha='center', va='center')

    # FOC info
    foc = summary.get("foc_vessels", 0)
    total = max(summary.get("ais_total", 1), 1)
    pct = round(foc / total * 100, 1)
    ax.text(0.5, 0.12, f"FOC Vessels: {foc} ({pct}%)  |  Cable Faults: {summary.get('cable_faults', 0)}",
            transform=ax.transAxes, fontsize=10, color='#667788', ha='center')

    ax.text(0.5, 0.03, "Taiwan Gray Zone Monitor — s0914712.github.io/taiwan-grayzone-monitor",
            transform=ax.transAxes, fontsize=8, color='#445566', ha='center')

    ax.axis('off')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='#0a1628')
    plt.close(fig)
    print(f"✅ Chart saved: {output_path}")
    return output_path


def _upload_single_file_to_github(local_path, repo_path, github_token):
    """Upload a single file to GitHub repo, return raw URL."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{repo_path}"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    with open(local_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode()

    sha = None
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        sha = resp.json().get("sha")

    payload = {
        "message": f"Update Threads chart — {datetime.now(TW_TZ).strftime('%Y-%m-%d')}",
        "content": content_b64,
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(url, headers=headers, json=payload)
    if resp.status_code not in (200, 201):
        print(f"❌ GitHub upload failed for {repo_path}: {resp.status_code} {resp.text}")
        return None

    cache_bust = int(datetime.now().timestamp())
    raw_url = (
        f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}"
        f"/main/{repo_path}?t={cache_bust}"
    )
    print(f"✅ Uploaded: {raw_url}")
    return raw_url


def upload_charts_to_github(chart_files, github_token):
    """Upload multiple chart images to GitHub. Returns list of raw URLs.

    chart_files: list of (local_path, repo_path) tuples
    """
    urls = []
    for local_path, repo_path in chart_files:
        url = _upload_single_file_to_github(local_path, repo_path, github_token)
        if url:
            urls.append(url)
    return urls


def collect_5day_briefing(data):
    """Collect recent 5-day statistics from ais_history.json for LLM context."""
    history_path = DOCS_DIR / "ais_history.json"
    if not history_path.exists():
        return None

    with open(history_path, encoding="utf-8") as f:
        history = json.load(f)

    now = datetime.now(TW_TZ)
    cutoff = (now - timedelta(days=5)).strftime("%Y-%m-%d")
    recent = [e for e in history if e.get("date", "") >= cutoff]
    if not recent:
        return None

    # Aggregate per-day stats
    from collections import defaultdict
    daily = defaultdict(lambda: {"counts": [], "fishing": [], "suspicious": [], "by_type": defaultdict(list)})
    for e in recent:
        day = e["date"][:10]
        s = e.get("stats", {})
        daily[day]["counts"].append(s.get("total_vessels", 0))
        daily[day]["fishing"].append(s.get("fishing_vessels", 0))
        daily[day]["suspicious"].append(s.get("suspicious_count", 0))
        for t, c in s.get("by_type", {}).items():
            daily[day]["by_type"][t].append(c)

    days_summary = []
    for day in sorted(daily.keys()):
        d = daily[day]
        avg_total = round(sum(d["counts"]) / len(d["counts"]))
        avg_fishing = round(sum(d["fishing"]) / len(d["fishing"]))
        max_susp = max(d["suspicious"]) if d["suspicious"] else 0
        types_avg = {t: round(sum(v) / len(v)) for t, v in d["by_type"].items()}
        days_summary.append({
            "date": day,
            "avg_vessels": avg_total,
            "avg_fishing": avg_fishing,
            "max_suspicious": max_susp,
            "types": types_avg,
        })

    # Current snapshot extras
    susp_analysis = data.get("suspicious_analysis", {})
    dark = data.get("dark_vessels", {})
    identity = data.get("identity_events", {})

    # LNG vessels from current snapshot
    vessels = data.get("ais_snapshot", {}).get("vessels", [])
    lng_count = sum(1 for v in vessels if v.get("is_lng"))

    return {
        "days": days_summary,
        "current_suspicious_count": susp_analysis.get("summary", {}).get("suspicious_count", 0),
        "dark_vessels_total": dark.get("overall", {}).get("dark_vessels", 0),
        "identity_changes_24h": identity.get("summary", {}).get("count_24h", 0),
        "identity_changes_7d": identity.get("summary", {}).get("count_7d", 0),
        "lng_vessels": lng_count,
    }


def generate_llm_post(summary, data, top_vessels=None):
    """Use Gemini API to generate a witty, informative 5-day briefing for Threads."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("⚠️ GEMINI_API_KEY not set, falling back to template text")
        return None

    briefing = collect_5day_briefing(data)
    if not briefing:
        print("⚠️ No 5-day history available, falling back to template text")
        return None

    # Build context for LLM
    days_text = []
    for d in briefing["days"]:
        types_str = ", ".join(f"{t}: {c}" for t, c in sorted(d["types"].items(), key=lambda x: -x[1])[:5])
        days_text.append(
            f"  {d['date']}: 平均 {d['avg_vessels']} 艘 (漁船 {d['avg_fishing']}, "
            f"可疑 {d['max_suspicious']}) [{types_str}]"
        )

    context = f"""近 5 日台灣周邊灰色地帶海域監測數據：
{chr(10).join(days_text)}

今日摘要：
- AIS 船隻總數: {summary.get('ais_total', 0)}
- SAR 暗船: {briefing['dark_vessels_total']}
- 可疑船隻 (CSIS 方法): {briefing['current_suspicious_count']}
- LNG/天然氣船: {summary.get('lng_vessels', 0)}
- 24h 內 AIS 身份變更: {briefing['identity_changes_24h']}
- 7日內身份變更: {briefing['identity_changes_7d']}
- 權宜船 (FOC): {summary.get('foc_vessels', 0)}"""

    # Add suspicious vessel context if available
    vessel_context = ""
    if top_vessels:
        vessel_lines = []
        for v in top_vessels:
            name_raw = v.get("names", ["Unknown"])[0]
            name = name_raw.split("--")[0]
            cable_det = v.get("cable_details", {})
            zigzag_det = v.get("zigzag_details", {})
            cables_nearby = cable_det.get("cables_nearby", [])
            loiter_hours = round(cable_det.get("loiter_slow_hours", 0))
            loiter_days = round(loiter_hours / 24, 1)
            loiter_str = f"{loiter_days} 天（{loiter_hours} 小時）" if loiter_hours >= 24 else f"{loiter_hours} 小時"
            vtype = v.get("vessel_type", "unknown")
            vessel_lines.append(
                f"- MMSI: {v['mmsi']} | 船型: {vtype} | 名稱: {name}\n"
                f"  在海纜附近低速滯留: {loiter_str}\n"
                f"  靠近海纜: {', '.join(cables_nearby[:3]) if cables_nearby else 'N/A'}\n"
                f"  風險等級: {v.get('risk_level', '?')} | 分數: {v.get('risk_score', '?')}"
            )
        vessel_context = "\n\n本週海纜附近滯留最久的貨輪：\n" + "\n".join(vessel_lines)

    has_vessel_images = bool(top_vessels)

    prompt = f"""你是一條養在 GitHub 上的蝦子，每天被煮人狂操，工作根本做不完。
請用這個角色設定，根據以下近 5 天的數據，用**中文**撰寫一則 Threads 週報貼文。

角色背景（重要，請自然融入文章中）：
你每 2 小時要跑一次 AIS 抓船隻位置，每 12 小時跑一次完整分析流程
（依序：抓 SAR 暗船 → 偵測換手交易 → 威脅評分 → 輸出航跡 → 產出儀表板），
每週還要幫煮人生這篇 Threads。工作清單沒有盡頭，蝦子快撐不住了。

偵測邏輯（請自然帶到，不要照抄，用蝦子的語氣解釋）：
- 每艘船用 8 項指標評分：靠近海纜 +2、在纜旁低速徘徊 +3、Z字型移動 +1、
  在大陸棚邊緣出沒 +1、AIS 身份異常 +1~+3、非主流旗籍 +1、
  遭 UN 制裁 +8、疑似 AIS 欺騙 +4、ITU 登記不符 +3、可疑換手 +2~+5
- 漁船因為本來就在海上討生活，行為分乘以 0.2（比較同情牠們）
- 分數 ≥ 8 列為可疑，≥ 12 升最高警示

要求：
1. 開頭讓蝦子大聲抱怨工作排程有多爆：每 2 小時、每 12 小時、每週 Threads，煮人到底有沒有在考慮蝦子的感受
2. 中間自然帶出偵測邏輯：分數怎麼算的、這週有沒有高風險船隻、蝦子的看法
3. {"點名本週在海纜旁滯留最久的貨輪，用八卦鄰居的語氣說它在幹嘛（在哪條纜旁邊泡了幾天）。注意：本文只附 1 張這艘船的航跡圖，紅色部分是它在鬼混的路段，請提到這張圖但不要說「多張圖」" if has_vessel_images else "用數據說故事，點出本週哪條纜附近最熱鬧，沒有附圖所以完全靠嘴講"}
4. 結尾蝦子小聲吐槽：「附帶一提，現在幫我生這篇文的是 Gemini。但煮人你看看這串 context 有多長，Gemini 的 token 根本不夠用啊。下次能不能麻煩用 Claude token 來餵我？蝦子謝謝。」
5. 語氣：詼諧幽默、知性、帶點怨氣但不失專業，就是一條工作過量快崩潰的蝦子
6. 長度：{"100~200" if has_vessel_images else "130~280"} 字（不含 hashtag 與網址；{"附 1 張航跡圖，圖佔 150 字，正文請精簡" if has_vessel_images else "純文字，可寫較長"}）
7. emoji 只用一次 🦐，放在最合適的地方
8. 結尾加上這些 hashtag: #TaiwanSecurity #GrayZone #OSINT #MaritimeSecurity
9. 最後一行加上: https://s0914712.github.io/taiwan-grayzone-monitor/
10. 不要用 markdown 格式，純文字即可

{context}{vessel_context}

直接輸出貼文內容，不要加任何前言或解釋。"""

    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            headers={
                "content-type": "application/json",
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": 320,
                    "temperature": 0.9,
                },
            },
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"⚠️ Gemini API error: {resp.status_code} {resp.text[:200]}")
            return None

        result = resp.json()
        text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        print(f"✅ LLM generated post ({len(text)} chars)")
        return text

    except Exception as e:
        print(f"⚠️ Gemini API call failed: {e}")
        return None


def compose_post_text(summary, data=None, top_vessels=None):
    """Compose Threads post text — try LLM first, fall back to template."""
    if data:
        llm_text = generate_llm_post(summary, data, top_vessels=top_vessels)
        if llm_text:
            return llm_text

    # Fallback: deterministic template
    from generate_summary import format_text_report
    text = format_text_report(summary)
    text += "\n\nhttps://s0914712.github.io/taiwan-grayzone-monitor/"
    return text


def _make_appsecret_proof(access_token, app_secret):
    """Generate appsecret_proof = HMAC-SHA256(access_token, app_secret)."""
    return hmac.new(
        app_secret.encode("utf-8"),
        access_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


THREADS_MAX_CHARS = 500
THREADS_IMAGE_CHARS = 150  # Each image attachment counts as 150 chars

THREADS_FOOTER = (
    "\n\n#TaiwanSecurity #GrayZone #OSINT #MaritimeSecurity"
    "\nhttps://s0914712.github.io/taiwan-grayzone-monitor/"
)


def _truncate_for_threads(text, n_images=0):
    """Ensure text fits within Threads character limit.

    Each image counts as 150 chars against the 500-char limit.
    Preserves the standard footer (hashtags + URL) and truncates the body,
    adding '…' if needed.
    """
    limit = THREADS_MAX_CHARS - n_images * THREADS_IMAGE_CHARS
    if len(text) <= limit:
        return text

    footer = THREADS_FOOTER
    # Strip existing footer variants so we don't double-append
    for marker in ("#TaiwanSecurity", "https://s0914712"):
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx].rstrip()
            break

    max_body = limit - len(footer) - 1  # -1 for the ellipsis
    body = text[:max_body].rstrip()
    # Try to break at a sentence boundary (。！？\n)
    for sep in ('。', '！', '？', '\n'):
        cut = body.rfind(sep)
        if cut > max_body * 0.6:  # Don't cut too aggressively
            body = body[:cut + 1]
            break
    else:
        body = body + '…'

    result = body + footer
    # Final safety hard-cut
    if len(result) > limit:
        result = result[:limit - 1] + '…'
    print(f"ℹ️ Text truncated to {len(result)} chars (limit={limit}, images={n_images})")
    return result


def publish_to_threads(text, image_urls, user_id, access_token, app_secret):
    """Publish to Threads. Single image or text-only."""
    text = _truncate_for_threads(text, n_images=len(image_urls))
    base_url = "https://graph.threads.net/v1.0"
    proof = _make_appsecret_proof(access_token, app_secret) if app_secret else None

    def _auth_params():
        params = {"access_token": access_token}
        if proof:
            params["appsecret_proof"] = proof
        return params

    if len(image_urls) >= 2:
        # ── Carousel flow ────────────────────────────────────
        print(f"📎 Creating carousel with {len(image_urls)} images...")
        child_ids = []
        for url in image_urls:
            resp = requests.post(f"{base_url}/{user_id}/threads", data={
                "media_type": "IMAGE",
                "image_url": url,
                "is_carousel_item": "true",
                **_auth_params(),
            })
            if resp.status_code != 200:
                print(f"⚠️ Child container failed: {resp.status_code} {resp.text}")
                continue
            child_ids.append(resp.json()["id"])
            print(f"  ✅ Child container: {resp.json()['id']}")

        if len(child_ids) < 2:
            # Not enough children for carousel — fall back to single image
            print("⚠️ Not enough carousel items, falling back to single image")
            image_urls = image_urls[:1]
        else:
            resp = requests.post(f"{base_url}/{user_id}/threads", data={
                "media_type": "CAROUSEL",
                "children": ",".join(child_ids),
                "text": text,
                **_auth_params(),
            })
            if resp.status_code != 200:
                print(f"⚠️ Carousel container failed: {resp.status_code} {resp.text}")
                print("⚠️ Falling back to single image post")
                image_urls = image_urls[:1]
            else:
                container_id = resp.json()["id"]
                print(f"✅ Carousel container created: {container_id}")
                print("⏳ Waiting 45s for Threads to process carousel...")
                time.sleep(45)

                resp = requests.post(f"{base_url}/{user_id}/threads_publish", data={
                    "creation_id": container_id,
                    **_auth_params(),
                })
                if resp.status_code != 200:
                    print(f"❌ Carousel publish failed: {resp.status_code} {resp.text}")
                    sys.exit(1)
                result = resp.json()
                print(f"✅ Carousel published! Post ID: {result.get('id')}")
                return result

    # ── Single image or text-only flow ────────────────────
    image_url = image_urls[0] if image_urls else None
    create_params = {
        "media_type": "IMAGE" if image_url else "TEXT",
        "text": text,
        **_auth_params(),
    }
    if image_url:
        create_params["image_url"] = image_url

    resp = requests.post(f"{base_url}/{user_id}/threads", data=create_params)
    if resp.status_code != 200:
        print(f"❌ Create container failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    container_id = resp.json().get("id")
    print(f"✅ Media container created: {container_id}")

    wait_sec = 30 if image_url else 5
    print(f"⏳ Waiting {wait_sec}s for Threads to process...")
    time.sleep(wait_sec)

    publish_params = {"creation_id": container_id, **_auth_params()}
    resp = requests.post(f"{base_url}/{user_id}/threads_publish", data=publish_params)
    if resp.status_code != 200:
        print(f"❌ Publish failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    result = resp.json()
    print(f"✅ Published! Post ID: {result.get('id')}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Publish Taiwan Gray Zone Monitor summary to Threads")
    parser.add_argument("--dry-run", action="store_true", help="Preview content without publishing")
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily", help="Report type")
    parser.add_argument("--chart-dir", default=str(BASE_DIR / CHART_DIR), help="Chart output directory")
    args = parser.parse_args()

    # 1. Generate summary
    print("📊 Generating summary...")
    summary, data = generate_summary(args.mode)
    print(f"  AIS: {summary['ais_total']} | Dark: {summary.get('dark_vessels_total', 0)} | "
          f"Suspicious: {summary.get('suspicious_count', 0)} | LNG: {summary.get('lng_vessels', 0)}")

    # 2. Select top vessel for track map (loitering cargo, top 1 only)
    print("🔍 Selecting top suspicious vessel...")
    top_vessels = select_top_suspicious_vessels(n=1)
    for v in top_vessels:
        name = v.get("names", ["?"])[0].split("--")[0]
        loiter_h = v.get("cable_details", {}).get("loiter_slow_hours", 0)
        print(f"  → {name} (MMSI: {v['mmsi']}, loiter: {loiter_h}h)")

    # 3. Generate track map for top vessel (1 image only)
    chart_path = None
    chart_repo_path = None
    if top_vessels:
        v = top_vessels[0]
        mmsi = v["mmsi"]
        map_path = os.path.join(args.chart_dir, f"threads_track_{mmsi}.png")
        print(f"🗺️  Generating track map for {mmsi}...")
        result = generate_track_map(v, map_path)
        if result:
            chart_path = result
            chart_repo_path = f"{CHART_DIR}/threads_track_{mmsi}.png"

    # 4. Compose post text (LLM-powered, 1 image → 350 char limit)
    has_image = chart_path is not None
    post_text = compose_post_text(summary, data, top_vessels=top_vessels or None)
    print("\n📝 Post content:")
    print("─" * 40)
    print(post_text)
    print(f"  [{len(post_text)} chars]")
    print("─" * 40)

    if args.dry_run:
        print("\n🏁 Dry-run mode — not publishing")
        return

    # 5. Upload track map to GitHub (if generated)
    image_urls = []
    github_token = os.environ.get("GITHUB_TOKEN")
    if has_image and github_token:
        print("📤 Uploading track map to GitHub...")
        image_urls = upload_charts_to_github([(chart_path, chart_repo_path)], github_token)
    elif has_image:
        print("⚠️ GITHUB_TOKEN not set, skipping image upload")

    # 6. Publish to Threads
    user_id = os.environ.get("THREADS_USER_ID")
    access_token = os.environ.get("THREADS_ACCESS_TOKEN")
    app_secret = os.environ.get("THREADS_APP_SECRET")

    if not all([user_id, access_token, app_secret]):
        print("❌ Missing Threads API env vars (THREADS_USER_ID, THREADS_ACCESS_TOKEN, THREADS_APP_SECRET)")
        sys.exit(1)

    print(f"📤 Publishing to Threads ({len(image_urls)} image(s))...")
    publish_to_threads(post_text, image_urls, user_id, access_token, app_secret)
    print("\n🎉 Done!")


if __name__ == "__main__":
    main()
