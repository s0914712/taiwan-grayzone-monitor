#!/usr/bin/env python3
"""OG / social-share banner generator — Taiwan Gray Zone Monitor.

產生 1200×630 的深色主題社群分享橫幅 (`docs/og-banner.png`)，供全站
`og:image` / `twitter:image` 使用。

底圖是**真實的台灣周邊監測畫面**（非示意圖）：
  * 台灣 / 東沙領海基線輪廓 —— docs/data/territorial_baseline.json
  * 真實海底電纜路由 —— data/cable-geo.json
  * 即時船位（全部淡點 + 可疑船紅點）—— docs/data.json（若存在）
左側疊暗漸層放中英標題，確保文字可讀。

配色沿用前端設計系統。CJK 標題需 WenQuanYi/Noto 字型，否則退回 DejaVu。
資料變動不影響品牌訊息，屬一次性/偶爾重跑的 committed 資產。
用法：python3 src/generate_og_image.py [-o docs/og-banner.png]
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon

_REPO = Path(__file__).resolve().parent.parent
BASELINE_FILE = _REPO / "docs" / "data" / "territorial_baseline.json"
CABLE_FILE = _REPO / "data" / "cable-geo.json"
DATA_JSON = _REPO / "docs" / "data.json"

# 設計系統配色
CYAN = '#00f5ff'
TEXT = '#e8eef7'
MUTED = '#8aa4c8'
RED = '#ff3366'
GOLD = '#ffd700'
LAND = '#1b2740'
LAND_EDGE = '#3a5a80'

W, H = 1200, 630
# 監測範圍（含台灣海峽、福建沿岸、東北外海海纜樞紐）— 比例貼近 1200:630
LON0, LON1 = 116.0, 125.6
LAT0, LAT1 = 21.3, 26.0


def _font():
    plt.rcParams['font.sans-serif'] = [
        'WenQuanYi Zen Hei', 'Noto Sans CJK TC', 'Noto Sans CJK SC',
        'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False


def _load_baselines():
    if not BASELINE_FILE.exists():
        return []
    d = json.loads(BASELINE_FILE.read_text(encoding='utf-8'))
    return [pts for pts in d.values() if isinstance(pts, list) and len(pts) >= 3]


def _load_cables():
    if not CABLE_FILE.exists():
        return []
    d = json.loads(CABLE_FILE.read_text(encoding='utf-8'))
    out = []
    for f in d.get('features', []):
        for seg in f.get('geometry', {}).get('coordinates', []):
            pts = [(lo, la) for lo, la in seg
                   if LAT0 <= la <= LAT1 and LON0 <= lo <= LON1]
            if len(pts) >= 2:
                out.append(pts)
    return out


def _load_vessels():
    """回傳 (all_pts, suspicious_pts) 於 bbox 內。"""
    if not DATA_JSON.exists():
        return [], []
    d = json.loads(DATA_JSON.read_text(encoding='utf-8'))
    def inbox(la, lo):
        return la is not None and lo is not None and LAT0 <= la <= LAT1 and LON0 <= lo <= LON1
    allp = [(v['lon'], v['lat']) for v in d.get('ais_snapshot', {}).get('vessels', [])
            if inbox(v.get('lat'), v.get('lon'))]
    susp = [(v['last_lon'], v['last_lat'])
            for v in d.get('suspicious_analysis', {}).get('suspicious_vessels', [])
            if inbox(v.get('last_lat'), v.get('last_lon'))]
    return allp, susp


def generate(out_path):
    _font()
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(LON0, LON1)
    ax.set_ylim(LAT0, LAT1)
    ax.axis('off')

    # 海洋漸層背景
    grad = np.linspace(0, 1, 256).reshape(-1, 1)
    top = np.array([10, 15, 28]) / 255
    bot = np.array([16, 26, 46]) / 255
    rgb = top[None, None, :] * (1 - grad[:, :, None]) + bot[None, None, :] * grad[:, :, None]
    ax.imshow(np.tile(rgb, (1, 2, 1)), extent=[LON0, LON1, LAT0, LAT1],
              aspect='auto', zorder=0, origin='lower')

    # 陸地（台灣 / 東沙 領海基線輪廓）
    for pts in _load_baselines():
        ax.add_patch(Polygon(pts, closed=True, facecolor=LAND,
                             edgecolor=LAND_EDGE, lw=1.2, zorder=2, alpha=0.95))

    # 即時船位（全部淡點）
    allp, susp = _load_vessels()
    if allp:
        xs, ys = zip(*allp)
        ax.scatter(xs, ys, s=2.2, c=CYAN, alpha=0.20, zorder=3, linewidths=0)

    # 真實海纜路由
    for pts in _load_cables():
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=GOLD, alpha=0.55, lw=1.3, zorder=4)

    # 可疑船（紅點 + 光暈）
    if susp:
        xs, ys = zip(*susp)
        ax.scatter(xs, ys, s=42, c=RED, alpha=0.18, zorder=5, linewidths=0)
        ax.scatter(xs, ys, s=11, c=RED, alpha=0.95, zorder=6,
                   edgecolors='white', linewidths=0.4)

    # 左側暗漸層面板（讓標題可讀）——蓋在海峽/福建側，不擋台灣本島
    panel = np.zeros((10, 256, 4))
    panel[..., 3] = np.linspace(0.92, 0.0, 256)[None, :]  # 左黑→右透明
    ax.imshow(panel, extent=[LON0, LON0 + 5.6, LAT0, LAT1],
              aspect='auto', zorder=7, origin='lower')

    # ── 文字（用 axes 分數座標，與地圖無關）──
    def T(x, y, s, **kw):
        ax.text(x, y, s, transform=ax.transAxes, zorder=8, **kw)

    ax.add_patch(plt.Rectangle((0.045, 0.40), 0.006, 0.24, transform=ax.transAxes,
                               facecolor=CYAN, zorder=8, clip_on=False))
    T(0.075, 0.60, '台灣灰色地帶', color=TEXT, fontsize=44, fontweight='bold', va='center')
    T(0.075, 0.485, '與海底電纜監測', color=CYAN, fontsize=44, fontweight='bold', va='center')
    T(0.077, 0.395, 'Taiwan Gray Zone & Submarine Cable Monitor',
      color=MUTED, fontsize=17, va='center')
    ax.plot([0.077, 0.55], [0.35, 0.35], transform=ax.transAxes,
            color=CYAN, alpha=0.3, lw=1, zorder=8)
    T(0.077, 0.31, 'OSINT · AIS · SAR 衛星暗船 · STS 旁靠 · 海底電纜威脅偵測',
      color=MUTED, fontsize=13, va='center')

    # 圖例（海纜用畫線，避免字型缺字）
    T(0.077, 0.15, '● 可疑船', color=RED, fontsize=12, va='center')
    ax.plot([0.205, 0.235], [0.15, 0.15], transform=ax.transAxes,
            color=GOLD, lw=2, zorder=8, clip_on=False)
    T(0.245, 0.15, '海底電纜', color=GOLD, fontsize=12, va='center')
    T(0.077, 0.075, 's0914712.github.io/taiwan-grayzone-monitor',
      color=CYAN, alpha=0.75, fontsize=12, va='center')

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=100)
    plt.close(fig)
    print(f'🖼️  OG banner (real map) written: {out_path} ({W}×{H}) '
          f'| cables + {len(susp)} suspicious over {len(allp)} vessels')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-o', '--out',
                    default=str(_REPO / 'docs' / 'og-banner.png'))
    args = ap.parse_args()
    generate(args.out)


if __name__ == '__main__':
    main()
