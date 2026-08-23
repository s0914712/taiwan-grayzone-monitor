#!/usr/bin/env python3
"""
中國公務/特殊關注船歷史航跡圖產生器 — Taiwan Gray Zone Monitor

掃描 data/vessel_routes/ 內所有逐船航跡檔，挑出中國公務/特殊關注船並依子類別
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
ROUTES_DIR = DATA_DIR / "vessel_routes"

sys.path.insert(0, str(SRC_DIR))
import supabase_store  # noqa: E402
from fetch_ais_data import classify_gov_vessel  # noqa: E402
from map_basemap import TAIWAN_COASTLINE, draw_cables, draw_land  # noqa: E402,F401

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

# 台灣本島範圍 (lat_min, lat_max, lon_min, lon_max)：單日圖用來當作視野下限，
# 確保無論船隻聚在哪，圖上都看得到台灣、判讀得出相對位置
TAIWAN_VIEW_BOUNDS = (21.9, 25.3, 120.0, 122.0)

# 每類別最多標註船名的艘數（依軌跡點數取前 N）——公務船從 10 艘成長到 80+ 艘後
# 全部標註會讓沿岸區域變成一團文字；未入選者仍繪製航跡但不標字
MAX_LABELS_PER_CATEGORY = 4

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


def _iter_local_routes():
    """逐一 yield 本地航跡檔內容；回傳 (dict, unreadable_count)。"""
    unreadable = 0
    for path in glob.glob(str(ROUTES_DIR / "*.json")):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                yield json.load(f)
        except Exception:
            unreadable += 1
    if unreadable:
        print(f"⚠️ {unreadable} 個航跡檔無法解析，已略過")


def _iter_supabase_routes():
    """從 Supabase 取公務/科研船航跡。

    fetch_ais_data 已把公務船的 type_name 覆寫為子類別，所以用 type 過濾即可
    把下載量從「3 萬艘全撈」壓到數十艘。名稱分類仍在下方照跑一次，涵蓋
    type 未被覆寫的舊資料。
    """
    rows = supabase_store.fetch_routes_by_type(CATEGORY_ORDER)
    print(f"  ☁️  Supabase 取得 {len(rows)} 艘公務/科研船航跡")
    return rows


def _route_source():
    """優先用本地航跡檔；本地沒有（CI 未取 vessel-data）才走 Supabase。"""
    if ROUTES_DIR.is_dir() and any(ROUTES_DIR.glob("*.json")):
        return _iter_local_routes()
    if supabase_store.is_configured():
        return _iter_supabase_routes()
    print("⚠️ 找不到本地航跡檔，且 Supabase 未設定")
    return []


def find_gov_routes():
    """回傳公務/關注船航跡清單（含 category），來源為本地檔或 Supabase。"""
    vessels = []
    for d in _route_source():
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
    # 依類別、再依航跡點數排序
    order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    vessels.sort(key=lambda v: (order.get(v['category'], 9), -len(v['track'])))
    return vessels


def plot_tracks(vessels, output_path, title=None, max_labels=None, pad=0.5,
                include_bounds=None):
    """繪製公務/關注船航跡圖。

    title: 覆寫資訊框第一行（例如單日動態圖標明日期），預設為 14 天歷史航跡標題。
    max_labels: 每類別最多標註船名的艘數，預設 MAX_LABELS_PER_CATEGORY。
        可傳 dict 依類別分配配額（如 {'coastguard': 6, 'msa': 2}），讓主角類別
        全部標名、陪襯類別少標幾艘，避免沿岸擠成一團字。
    pad: 視野邊界留白（度）。船數少的單日圖留白大一點，船名標註才不會被切掉。
    include_bounds: (lat_min, lat_max, lon_min, lon_max)，強制納入視野的範圍。
        單日圖常常只有兩三艘船擠在廈門外海，若只用資料範圍會縮到看不出跟台灣的
        相對位置——傳入 TAIWAN_VIEW_BOUNDS 可確保台灣一定在畫面裡。
    """
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

    all_lats = [p['lat'] for v in vessels for p in v['track']]
    all_lons = [p['lon'] for v in vessels for p in v['track']]
    if include_bounds:
        inc_lat_min, inc_lat_max, inc_lon_min, inc_lon_max = include_bounds
        all_lats += [inc_lat_min, inc_lat_max]
        all_lons += [inc_lon_min, inc_lon_max]
    lat_min, lat_max = min(all_lats) - pad, max(all_lats) + pad
    lon_min, lon_max = min(all_lons) - pad, max(all_lons) + pad

    fig, ax = plt.subplots(figsize=(9, 9))
    fig.patch.set_facecolor('#0a1628')
    ax.set_facecolor('#0a1628')

    # 陸地底圖（真實海岸線：台灣＋中國沿岸＋離島）
    draw_land(ax, (lat_min, lat_max, lon_min, lon_max))

    # 海底電纜背景
    draw_cables(ax, (lat_min, lat_max, lon_min, lon_max))

    # 地理參考地名（僅繪製視野內者）
    for la, lo, label in LANDMARKS:
        if lat_min <= la <= lat_max and lon_min <= lo <= lon_max:
            ax.text(lo, la, label, fontsize=8, color='#6b86b0',
                    style='italic', ha='center', va='center', zorder=2,
                    alpha=0.85)

    # 描邊樣式，讓航跡旁的船名在暗底上清晰可讀
    stroke = [pe.withStroke(linewidth=2.2, foreground='#0a1628')]

    # 每類別依軌跡點數取前 N 艘標註船名（vessels 已依類別+點數排序）
    def _quota(category):
        if max_labels is None:
            return MAX_LABELS_PER_CATEGORY
        if isinstance(max_labels, dict):
            return max_labels.get(category, MAX_LABELS_PER_CATEGORY)
        return max_labels

    label_counts = {}
    labeled = set()
    for v in vessels:
        c = v['category']
        if label_counts.get(c, 0) < _quota(c):
            label_counts[c] = label_counts.get(c, 0) + 1
            labeled.add(id(v))

    # 逐艘航跡（依子類別著色；入選者在終點旁標示船名 + 位置）
    for v in vessels:
        color = CATEGORY_COLOR.get(v['category'], '#888888')
        has_label = id(v) in labeled
        lats = [p['lat'] for p in v['track']]
        lons = [p['lon'] for p in v['track']]
        alpha = 0.9 if has_label else 0.45
        ax.plot(lons, lats, color=color, linewidth=1.6 if has_label else 1.0,
                alpha=alpha, marker='o', markersize=2.5 if has_label else 1.5,
                zorder=3)
        ax.plot(lons[0], lats[0], 'o', color=color,
                markersize=7 if has_label else 4, zorder=4, alpha=alpha,
                markeredgecolor='white', markeredgewidth=0.6)
        ax.plot(lons[-1], lats[-1], 's', color=color,
                markersize=7 if has_label else 4, zorder=4, alpha=alpha,
                markeredgecolor='white', markeredgewidth=0.6)
        if not has_label:
            continue
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
    info_lines = [title or
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
