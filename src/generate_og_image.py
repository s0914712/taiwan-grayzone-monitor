#!/usr/bin/env python3
"""OG / social-share banner generator — Taiwan Gray Zone Monitor.

產生 1200×630 的深色主題社群分享橫幅 (`docs/og-banner.png`)，供全站
`og:image` / `twitter:image` 使用。這是**靜態品牌圖**，不隨資料變動，
因此為一次性產生的 committed 資產（非 CI 每次重跑）。改標語/配色時再執行。

配色沿用前端設計系統（bg #0a0f1c、accent-cyan #00f5ff、船型色）。
CJK 標題需 WenQuanYi/Noto 字型，否則退回 DejaVu（中文會缺字）。

用法：python3 src/generate_og_image.py [-o docs/og-banner.png]
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import RegularPolygon, FancyArrow

# 設計系統配色
BG_TOP = '#0a0f1c'
BG_BOTTOM = '#141e32'
CYAN = '#00f5ff'
TEXT = '#e8eef7'
MUTED = '#8aa4c8'
# 船型色（fishing / cargo / tanker / research / other）
VESSEL_COLORS = ['#00ff88', '#00f5ff', '#ff6b35', '#c77dff', '#ff3366']

W, H = 1200, 630


def _setup_font():
    plt.rcParams['font.sans-serif'] = [
        'WenQuanYi Zen Hei', 'Noto Sans CJK TC', 'Noto Sans CJK SC',
        'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False


def generate(out_path):
    _setup_font()
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis('off')

    # 垂直漸層背景
    grad = np.linspace(0, 1, H).reshape(-1, 1)
    top = np.array([10, 15, 28]) / 255
    bot = np.array([20, 30, 50]) / 255
    rgb = top[None, None, :] * (1 - grad[:, :, None]) + bot[None, None, :] * grad[:, :, None]
    ax.imshow(np.tile(rgb, (1, W, 1)), extent=[0, W, 0, H], aspect='auto', zorder=0)

    # 右側同心測距圈（雷達感）
    cx, cy = 1010, 300
    for r in (70, 130, 190, 250):
        ax.add_patch(plt.Circle((cx, cy), r, fill=False,
                                 edgecolor=CYAN, alpha=0.06, lw=1.2, zorder=1))

    # 弧形海纜線（虛線）
    xs = np.linspace(60, 1160, 200)
    ys = 130 + 55 * np.sin((xs - 60) / 1100 * np.pi * 1.4)
    ax.plot(xs, ys, color=CYAN, alpha=0.22, lw=1.6, ls=(0, (6, 5)), zorder=1)
    ax.text(70, 96, '⎯⎯ submarine cable', color=MUTED, alpha=0.5, fontsize=9,
            zorder=2)

    # 幾個船隻三角標（右側雷達區）
    rng = np.random.default_rng(7)
    for i, color in enumerate(VESSEL_COLORS):
        ang = rng.uniform(0, 2 * np.pi)
        rr = rng.uniform(50, 230)
        px, py = cx + rr * np.cos(ang), cy + rr * np.sin(ang)
        ax.add_patch(RegularPolygon((px, py), numVertices=3, radius=13,
                                    orientation=rng.uniform(0, np.pi),
                                    facecolor=color, edgecolor='white',
                                    lw=0.8, alpha=0.92, zorder=4))
        ax.add_patch(plt.Circle((px, py), 22, fill=False, edgecolor=color,
                                 alpha=0.25, lw=1.2, zorder=3))

    # 左側 accent 直條
    ax.add_patch(plt.Rectangle((60, 250), 6, 150, facecolor=CYAN, zorder=5))

    # 標題（中）
    ax.text(90, 388, '台灣灰色地帶', color=TEXT, fontsize=52,
            fontweight='bold', va='center', zorder=5)
    ax.text(90, 322, '與海底電纜監測', color=CYAN, fontsize=52,
            fontweight='bold', va='center', zorder=5)
    # 標題（英）
    ax.text(92, 262, 'Taiwan Gray Zone & Submarine Cable Monitor',
            color=MUTED, fontsize=20, va='center', zorder=5)

    # 分隔線 + 標語
    ax.plot([92, 640], [225, 225], color=CYAN, alpha=0.3, lw=1, zorder=5)
    ax.text(92, 198, 'OSINT · AIS · SAR 衛星暗船 · STS 旁靠 · 海底電纜威脅偵測',
            color=MUTED, fontsize=15, va='center', zorder=5)

    # 底部網址
    ax.text(92, 60, 's0914712.github.io/taiwan-grayzone-monitor',
            color=CYAN, alpha=0.7, fontsize=13, va='center', zorder=5)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=100, facecolor=BG_TOP)
    plt.close(fig)
    print(f'🖼️  OG banner written: {out_path} ({W}×{H})')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-o', '--out',
                    default=str(Path(__file__).resolve().parent.parent
                                / 'docs' / 'og-banner.png'))
    args = ap.parse_args()
    generate(args.out)


if __name__ == '__main__':
    main()
