#!/usr/bin/env python3
"""
海警船歷史航跡圖產生器 — Taiwan Gray Zone Monitor

掃描 docs/vessel_routes/ 內所有逐船航跡檔，挑出（中國）海警公務船，
將其 14 天歷史航跡疊繪於同一張暗色主題地圖上（含台灣輪廓與海底電纜背景）。

用法：
    python3 src/plot_coast_guard_tracks.py [-o 輸出路徑.png]

預設輸出：docs/coast_guard_tracks.png
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
DATA_DIR = BASE_DIR / "data"
SRC_DIR = BASE_DIR / "src"
ROUTES_DIR = DOCS_DIR / "vessel_routes"

sys.path.insert(0, str(SRC_DIR))
from fetch_ais_data import is_coast_guard_vessel  # noqa: E402

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

# 每艘海警船的航跡配色（白底海警船以亮色系區分）
TRACK_COLORS = [
    '#ffd700', '#00f5ff', '#ff6b35', '#c8ff3d',
    '#ff5e8a', '#9d7bff', '#00ff88', '#ff3366',
]


def _load_cable_segments():
    """讀取 data/cable-geo.json 取得海底電纜路徑作為背景。"""
    cable_file = DATA_DIR / "cable-geo.json"
    if not cable_file.exists():
        return []
    try:
        with open(cable_file, 'r', encoding='utf-8') as f:
            geo = json.load(f)
    except Exception:
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


def find_coast_guard_routes():
    """掃描所有逐船航跡檔，回傳海警船航跡清單（依航跡點數排序）。"""
    vessels = []
    for path in glob.glob(str(ROUTES_DIR / "*.json")):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            continue
        name = d.get('name', '')
        mmsi = d.get('mmsi', '')
        if not is_coast_guard_vessel(name, mmsi):
            continue
        track = d.get('track', [])
        if not track:
            continue
        vessels.append({'name': name, 'mmsi': mmsi, 'track': track})
    vessels.sort(key=lambda v: len(v['track']), reverse=True)
    return vessels


def plot_tracks(vessels, output_path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    if not vessels:
        print("⚠️ 找不到任何海警船航跡，略過繪圖")
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

    # 逐艘海警船航跡
    for i, v in enumerate(vessels):
        color = TRACK_COLORS[i % len(TRACK_COLORS)]
        lats = [p['lat'] for p in v['track']]
        lons = [p['lon'] for p in v['track']]
        label = f"{v['name']} ({v['mmsi']})"
        ax.plot(lons, lats, color=color, linewidth=1.8, alpha=0.9,
                marker='o', markersize=3, zorder=3, label=label)
        # 起點（圓）/ 終點（方）
        ax.plot(lons[0], lats[0], 'o', color=color, markersize=8, zorder=4,
                markeredgecolor='white', markeredgewidth=0.6)
        ax.plot(lons[-1], lats[-1], 's', color=color, markersize=8, zorder=4,
                markeredgecolor='white', markeredgewidth=0.6)

    # 標題與資訊框
    spans = []
    for v in vessels:
        ts = [p.get('t', '') for p in v['track'] if p.get('t')]
        spans.extend(ts)
    info_lines = [
        f"China Coast Guard Tracks  ({len(vessels)} vessels)",
    ]
    if spans:
        info_lines.append(f"Track window: {min(spans)[:10]} → {max(spans)[:10]}")
    ax.text(0.02, 0.98, "\n".join(info_lines), transform=ax.transAxes,
            fontsize=9, color='#e8eef7', verticalalignment='top',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#141e32', alpha=0.9,
                      edgecolor='#2a3a5a'), zorder=5)

    ax.plot([], [], '--', color='#00f5ff', alpha=0.4, linewidth=1,
            label='Submarine cables')
    ax.legend(loc='lower right', fontsize=7, facecolor='#141e32',
              edgecolor='#2a3a5a', labelcolor='#8aa4c8')

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
    print(f"✅ 海警船航跡圖已輸出: {output_path}")
    return output_path


def main():
    ap = argparse.ArgumentParser(description="產生海警船歷史航跡圖")
    ap.add_argument('-o', '--output', default=str(DOCS_DIR / "coast_guard_tracks.png"),
                    help="輸出 PNG 路徑")
    args = ap.parse_args()

    vessels = find_coast_guard_routes()
    print(f"🔎 偵測到 {len(vessels)} 艘海警船:")
    for v in vessels:
        print(f"   - {v['name']} ({v['mmsi']}) | {len(v['track'])} 航跡點")
    plot_tracks(vessels, args.output)


if __name__ == "__main__":
    main()
