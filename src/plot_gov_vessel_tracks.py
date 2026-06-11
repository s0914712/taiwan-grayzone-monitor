#!/usr/bin/env python3
"""
中國公務/特殊關注船歷史航跡圖產生器 — Taiwan Gray Zone Monitor

掃描 docs/vessel_routes/ 內所有逐船航跡檔，挑出中國公務/特殊關注船並依子類別
（海警 coastguard / 海巡 msa / 海救 rescue / 科研·情報 research）著色，
將其 14 天歷史航跡疊繪於同一張暗色主題地圖上（含台灣輪廓與海底電纜背景）。

用法：
    python3 src/plot_gov_vessel_tracks.py [-o 輸出路徑.png]

預設輸出：docs/cn_gov_vessel_tracks.png
"""
import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
DATA_DIR = BASE_DIR / "data"
SRC_DIR = BASE_DIR / "src"
ROUTES_DIR = DOCS_DIR / "vessel_routes"

sys.path.insert(0, str(SRC_DIR))
from fetch_ais_data import classify_gov_vessel  # noqa: E402

# 台灣本島簡化輪廓座標 (lat, lon) — 與 publish_threads.py 一致
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

# 各子類別配色（與前端 docs/js/map.js VESSEL_COLORS 一致）
CATEGORY_COLOR = {
    'coastguard': '#ffffff',  # 海警 (white)
    'msa':        '#4d9fff',  # 海巡 (blue)
    'rescue':     '#ff9500',  # 海救 (orange)
    'research':   '#c77dff',  # 科研/情報 (purple)
}
CATEGORY_LABEL = {
    'coastguard': 'Coast Guard (海警)',
    'msa':        'MSA Patrol (海巡)',
    'rescue':     'Rescue & Salvage (海救)',
    'research':   'Research / Intel (科研)',
}
CATEGORY_ORDER = ['coastguard', 'msa', 'rescue', 'research']

# 地理參考點 (lat, lon, 標籤) — 只繪製落在當前視野內者
LANDMARKS = [
    (23.75, 120.95, '台灣 Taiwan'),
    (24.10, 119.30, '台灣海峽'),
    (24.43, 118.32, '金門'),
    (26.16, 119.95, '馬祖'),
    (23.57, 119.62, '澎湖'),
    (22.45, 117.60, '台灣淺灘'),
    (21.60, 120.95, '巴士海峽'),
    (25.10, 119.45, '閩江口'),
    (25.10, 118.30, '福建'),
    (27.80, 122.00, '東海'),
    (21.40, 117.80, '南海北部'),
]


def _short_name(name):
    """縮短船名以利在航跡旁標示。"""
    s = re.sub(r'\s+', ' ', (name or '').strip())
    s = re.sub(r'CHINA\s*COAST\s*GUARD', 'CCG', s, flags=re.I)
    return s or '?'


def _load_cable_segments():
    """讀取 data/cable-geo.json 取得海底電纜路徑作為背景。"""
    cable_file = DATA_DIR / "cable-geo.json"
    if not cable_file.exists():
        return []
    try:
        with open(cable_file, 'r', encoding='utf-8') as f:
            geo = json.load(f)
    except Exception as e:
        print(f"⚠️ 讀取 {cable_file} 失敗: {e}")
        return []
    segments = []
    for feat in geo.get('features', []):
        geom = feat.get('geometry', {})
        if geom.get('type') != 'LineString':
            continue
        # GeoJSON 為 [lon, lat]
        pts = [(c[1], c[0]) for c in geom.get('coordinates', []) if len(c) >= 2]
        if pts:
            segments.append(pts)
    return segments


def find_gov_routes():
    """掃描所有逐船航跡檔，回傳公務/關注船航跡清單（含 category）。"""
    vessels = []
    unreadable = 0
    for path in glob.glob(str(ROUTES_DIR / "*.json")):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            unreadable += 1
            continue
        category = classify_gov_vessel(d.get('name', ''))
        if not category:
            continue
        track = d.get('track', [])
        if not track:
            continue
        vessels.append({
            'name': d.get('name', ''),
            'mmsi': d.get('mmsi', ''),
            'category': category,
            'track': track,
        })
    if unreadable:
        print(f"⚠️ {unreadable} 個航跡檔無法解析，已略過")
    # 依類別、再依航跡點數排序
    order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    vessels.sort(key=lambda v: (order.get(v['category'], 9), -len(v['track'])))
    return vessels


def plot_tracks(vessels, output_path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    from matplotlib.lines import Line2D

    # 啟用 CJK 字型（若系統有 WenQuanYi/Noto，否則退回 DejaVu）
    plt.rcParams['font.sans-serif'] = [
        'WenQuanYi Zen Hei', 'Noto Sans CJK TC', 'Noto Sans CJK SC', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    if not vessels:
        print("⚠️ 找不到任何公務/關注船航跡，略過繪圖")
        return None

    cable_segments = _load_cable_segments()

    all_lats = [p['lat'] for v in vessels for p in v['track']]
    all_lons = [p['lon'] for v in vessels for p in v['track']]
    pad = 0.5
    lat_min, lat_max = min(all_lats) - pad, max(all_lats) + pad
    lon_min, lon_max = min(all_lons) - pad, max(all_lons) + pad

    fig, ax = plt.subplots(figsize=(9, 9))
    fig.patch.set_facecolor('#0a1628')
    ax.set_facecolor('#0a1628')

    # 台灣輪廓
    tw_lats = [p[0] for p in TAIWAN_COASTLINE]
    tw_lons = [p[1] for p in TAIWAN_COASTLINE]
    ax.fill(tw_lons, tw_lats, facecolor='#1a2640', edgecolor='#2a3a5a',
            linewidth=1, zorder=1)

    # 海底電纜背景
    for pts in cable_segments:
        clats = [p[0] for p in pts]
        clons = [p[1] for p in pts]
        visible = any(lat_min - 0.5 <= la <= lat_max + 0.5 and
                      lon_min - 0.5 <= lo <= lon_max + 0.5
                      for la, lo in zip(clats, clons))
        if visible:
            ax.plot(clons, clats, color='#00f5ff', alpha=0.18, linewidth=0.7,
                    linestyle='--', zorder=2)

    # 地理參考地名（僅繪製視野內者）
    for la, lo, label in LANDMARKS:
        if lat_min <= la <= lat_max and lon_min <= lo <= lon_max:
            ax.text(lo, la, label, fontsize=8, color='#6b86b0',
                    style='italic', ha='center', va='center', zorder=2,
                    alpha=0.85)

    # 描邊樣式，讓航跡旁的船名在暗底上清晰可讀
    stroke = [pe.withStroke(linewidth=2.2, foreground='#0a1628')]

    # 逐艘航跡（依子類別著色，並在終點旁標示船名 + 位置）
    for v in vessels:
        color = CATEGORY_COLOR.get(v['category'], '#888888')
        lats = [p['lat'] for p in v['track']]
        lons = [p['lon'] for p in v['track']]
        ax.plot(lons, lats, color=color, linewidth=1.6, alpha=0.9,
                marker='o', markersize=2.5, zorder=3)
        ax.plot(lons[0], lats[0], 'o', color=color, markersize=7, zorder=4,
                markeredgecolor='white', markeredgewidth=0.6)
        ax.plot(lons[-1], lats[-1], 's', color=color, markersize=7, zorder=4,
                markeredgecolor='white', markeredgewidth=0.6)
        # 航跡旁標示：船名 + 最新位置座標（終點＝方形標記）
        label = f"{_short_name(v['name'])}\n{lats[-1]:.2f}N,{lons[-1]:.2f}E"
        ax.annotate(label, (lons[-1], lats[-1]),
                    textcoords='offset points', xytext=(6, 5),
                    fontsize=6, color=color, zorder=6,
                    path_effects=stroke)

    # 資訊框
    spans = [p.get('t', '') for v in vessels for p in v['track'] if p.get('t')]
    counts = {}
    for v in vessels:
        counts[v['category']] = counts.get(v['category'], 0) + 1
    info_lines = [
        f"China Gov / Special-interest Vessel Tracks  ({len(vessels)} vessels)"]
    if spans:
        info_lines.append(f"Track window: {min(spans)[:10]} → {max(spans)[:10]}")
    summary = "  ".join(f"{CATEGORY_LABEL[c].split(' (')[0]}: {counts[c]}"
                        for c in CATEGORY_ORDER if c in counts)
    if summary:
        info_lines.append(summary)
    ax.text(0.02, 0.98, "\n".join(info_lines), transform=ax.transAxes,
            fontsize=9, color='#e8eef7', verticalalignment='top',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#141e32', alpha=0.9,
                      edgecolor='#2a3a5a'), zorder=5)

    # 類別圖例（只列出現的類別）
    legend_handles = [
        Line2D([0], [0], color=CATEGORY_COLOR[c], lw=2, marker='o',
               markeredgecolor='white', markeredgewidth=0.5,
               label=CATEGORY_LABEL[c])
        for c in CATEGORY_ORDER if c in counts
    ]
    legend_handles.append(
        Line2D([0], [0], color='#00f5ff', lw=1, ls='--', alpha=0.5,
               label='Submarine cables'))
    ax.legend(handles=legend_handles, loc='lower right', fontsize=7,
              facecolor='#141e32', edgecolor='#2a3a5a', labelcolor='#8aa4c8')

    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.tick_params(colors='#2a3a5a', labelsize=7)
    ax.grid(True, color='#1a2a40', linewidth=0.5, alpha=0.5)
    for spine in ax.spines.values():
        spine.set_color('#2a3a5a')
    ax.set_aspect('equal')
    ax.set_xlabel('Longitude', color='#445566', fontsize=8)
    ax.set_ylabel('Latitude', color='#445566', fontsize=8)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    fig.savefig(output_path, dpi=200, bbox_inches='tight', facecolor='#0a1628')
    plt.close(fig)
    print(f"✅ 公務/關注船航跡圖已輸出: {output_path}")
    return output_path


def main():
    ap = argparse.ArgumentParser(description="產生中國公務/關注船歷史航跡圖")
    ap.add_argument('-o', '--output',
                    default=str(DOCS_DIR / "cn_gov_vessel_tracks.png"),
                    help="輸出 PNG 路徑")
    args = ap.parse_args()

    vessels = find_gov_routes()
    print(f"🔎 偵測到 {len(vessels)} 艘公務/關注船:")
    for v in vessels:
        print(f"   - [{v['category']:10}] {v['name']} ({v['mmsi']}) | "
              f"{len(v['track'])} 航跡點")
    plot_tracks(vessels, args.output)


if __name__ == "__main__":
    main()
