#!/usr/bin/env python3
"""
================================================================================
週/月報圖表 — 徘徊熱區地圖 + 船種／船籍統計圖
Report charts: loitering-hotspot map + vessel-type / flag-state breakdown
================================================================================

供 `SendMessage.py --mode weekly|monthly` 推送 LINE 時附圖。輸入是
`aggregate_highrisk.py` 產出的 `docs/reports/{weekly,monthly}/<label>.json`。

設計要點：
* **matplotlib 只在繪圖函式內 import** —— 測試 CI 只裝 requests+pytest，
  模組層級 import 會讓整包測試無法收集（`map_basemap.py` 亦是同樣理由完全
  不碰 matplotlib）。純函式（分位色階、視野計算）因此可獨立測試。
* **色階與前端同一組**：`HEAT_COLORS` / 分位切檔法都對齊
  `docs/js/weekly-report.js`，PNG 與網頁講的必須是同一件事。
* **分位數而非等距**：離島與本島的滯留時數量級差極大，等距分箱會把絕大多數
  格子擠進同一色（前端註解已載明此坑）。
* **標題用英文**：GitHub runner 少了 CJK 字型時整行標題不會變成豆腐方塊
  （沿用 `gov_daily_activity.build_daily_gov_map` 的既有做法）。
"""
import os

from map_basemap import draw_cables, draw_land

# 與 docs/js/weekly-report.js 的 HEAT_COLORS 相同（淡→深）
HEAT_COLORS = ['#3d3520', '#7a5c1e', '#b3701f', '#e05a33', '#ff2d55']
# 與前端 TYPE_COLORS / RISK_COLORS 相同
TYPE_COLORS = {
    'fishing': '#00ff88', 'cargo': '#00f5ff', 'tanker': '#ff6b35',
    'lng': '#f0e130', 'coastguard': '#ffffff', 'msa': '#4d9fff',
    'rescue': '#ff9500', 'research': '#c77dff', 'other': '#ff3366',
    'unknown': '#888',
}
RISK_COLORS = {'critical': '#ff2d55', 'high': '#ff7847', 'medium': '#ffab2e'}

# 圖面配色（與 plot_gov_vessel_tracks 一致）
BG = '#0a1628'
PANEL_BG = '#141e32'
EDGE = '#2a3a5a'
TEXT = '#e8eef7'
MUTED = '#8aa4c8'
GRID = '#1a2a40'

CELL_DEG = 0.1          # 與 grid_utils.grid_cell 相同
HOTSPOT_ALPHA = 0.45    # 與前端 fillOpacity 相同
CJK_FONTS = ['WenQuanYi Zen Hei', 'Noto Sans CJK TC', 'Noto Sans CJK SC',
             'DejaVu Sans']


# ══════════════════════════════════════════════════════════════════
# 純函式（不 import matplotlib，可直接單元測試）
# ══════════════════════════════════════════════════════════════════

def quantile_cuts(values, buckets):
    """分位切檔點（移植自前端 quantileScale）。

    回傳 buckets-1 個門檻值；`color_index` 以「<= 門檻」逐一比對。
    空清單回傳空 list（所有值都會落到第 0 檔）。
    """
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return []
    cuts = []
    for i in range(1, buckets):
        idx = min(len(vals) - 1, int(len(vals) * i / buckets))
        cuts.append(vals[idx])
    return cuts


def color_index(value, cuts):
    """依分位門檻回傳色階索引（0 = 最淡）。"""
    for i, cut in enumerate(cuts):
        if value <= cut:
            return i
    return len(cuts)


def color_for(value, cuts, palette=HEAT_COLORS):
    idx = min(color_index(value, cuts), len(palette) - 1)
    return palette[idx]


def hotspot_bounds(hotspots, pad=0.6, include_bounds=None):
    """由熱區格算出繪圖視野 `(lat_min, lat_max, lon_min, lon_max)`。

    注意順序是 **lat 在前** —— `map_basemap.draw_land/draw_cables` 的約定。
    `include_bounds`（通常是 TAIWAN_VIEW_BOUNDS）強制納入視野：熱區常整片
    聚在福建外海，不含台灣的話圖上沒有參照點、看不出相對位置。
    """
    lats = [h['lat'] for h in hotspots or [] if h.get('lat') is not None]
    lons = [h['lon'] for h in hotspots or [] if h.get('lon') is not None]
    if not lats or not lons:
        if include_bounds:
            return include_bounds
        return None
    lat_min, lat_max = min(lats) - pad, max(lats) + pad
    lon_min, lon_max = min(lons) - pad, max(lons) + pad
    if include_bounds:
        i_lat_min, i_lat_max, i_lon_min, i_lon_max = include_bounds
        lat_min, lat_max = min(lat_min, i_lat_min), max(lat_max, i_lat_max)
        lon_min, lon_max = min(lon_min, i_lon_min), max(lon_max, i_lon_max)
    return (lat_min, lat_max, lon_min, lon_max)


def legend_bins(hotspots, cuts, palette=HEAT_COLORS):
    """色階圖例的標籤：[(色碼, '≤ 12.3 h'), ...]，最後一檔是 '> 上限'。"""
    if not hotspots:
        return []
    out = []
    for i, cut in enumerate(cuts[:len(palette) - 1]):
        out.append((palette[i], f'≤ {cut:g} h'))
    if cuts:
        out.append((palette[min(len(cuts), len(palette) - 1)],
                    f'> {cuts[-1]:g} h'))
    else:
        out.append((palette[0], 'all'))
    return out


def top_types(summary, limit=8):
    """船種分布 → [(type, count)] 依數量降冪。"""
    by_type = (summary or {}).get('by_type') or {}
    return sorted(by_type.items(), key=lambda kv: -kv[1])[:limit]


def top_flags(summary, limit=8):
    """船籍分布 → [(mid, zh, count)] 依數量降冪。

    `by_flag` 的鍵是數字型字串；Python dict 保留插入順序（後端已排好），
    但仍明確再排一次，不倚賴上游順序。
    """
    by_flag = (summary or {}).get('by_flag') or {}
    rows = [(mid, (f or {}).get('zh') or (f or {}).get('en') or mid,
             (f or {}).get('count', 0))
            for mid, f in by_flag.items()]
    rows.sort(key=lambda r: (-r[2], r[0]))
    return rows[:limit]


# ══════════════════════════════════════════════════════════════════
# 繪圖（matplotlib 在函式內 import）
# ══════════════════════════════════════════════════════════════════

def _setup_matplotlib():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    # CJK 字型；runner 沒裝 fonts-wqy-zenhei 時退回 DejaVu（中文會變豆腐，
    # 因此標題一律用英文，中文只出現在可犧牲的圖例/標籤）
    plt.rcParams['font.sans-serif'] = CJK_FONTS
    plt.rcParams['axes.unicode_minus'] = False
    return plt


def _style_axes(ax, xlabel=None, ylabel=None):
    ax.set_facecolor(BG)
    ax.tick_params(colors=MUTED, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(EDGE)
    if xlabel:
        ax.set_xlabel(xlabel, color=MUTED, fontsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, color=MUTED, fontsize=9)


def draw_hotspot_cells(ax, hotspots, cuts, cell_deg=CELL_DEG):
    """把熱區格畫成矩形（純繪圖，ax 可用 stub 測試）。回傳畫出的格數。"""
    from matplotlib.patches import Rectangle
    drawn = 0
    for h in hotspots or []:
        lat, lon = h.get('lat'), h.get('lon')
        if lat is None or lon is None:
            continue
        color = color_for(h.get('loiter_hours') or 0, cuts)
        half = cell_deg / 2
        ax.add_patch(Rectangle(
            (lon - half, lat - half), cell_deg, cell_deg,
            facecolor=color, edgecolor=color, linewidth=0.8,
            alpha=HOTSPOT_ALPHA, zorder=3))
        drawn += 1
    return drawn


def draw_vessel_points(ax, vessels, limit=400):
    """高風險船最後位置。點畫小且半透明 —— 一期有數百艘，實心大點會整片蓋掉
    底下的熱區格，而熱區才是這張圖的主訊號（與前端同一取捨）。"""
    drawn = 0
    for v in (vessels or [])[:limit]:
        lat, lon = v.get('last_lat'), v.get('last_lon')
        if lat is None or lon is None:
            continue
        ax.scatter([lon], [lat], s=14 if v.get('risk_level') == 'critical' else 9,
                   c=TYPE_COLORS.get(v.get('vessel_type'), '#888'),
                   edgecolors=RISK_COLORS.get(v.get('risk_level'), '#ffab2e'),
                   linewidths=0.6, alpha=0.55, zorder=4)
        drawn += 1
    return drawn


def render_hotspot_map(report, output_path, title=None,
                       include_bounds=None):
    """徘徊熱區地圖 PNG。熱區為空時回傳 None（呼叫端改為不附此圖）。"""
    hotspots = (report or {}).get('hotspots') or []
    if not hotspots:
        return None

    if include_bounds is None:
        try:
            from plot_gov_vessel_tracks import TAIWAN_VIEW_BOUNDS
            include_bounds = TAIWAN_VIEW_BOUNDS
        except Exception:
            include_bounds = None

    bounds = hotspot_bounds(hotspots, include_bounds=include_bounds)
    if not bounds:
        return None
    lat_min, lat_max, lon_min, lon_max = bounds

    plt = _setup_matplotlib()
    from matplotlib.patches import Patch

    fig, ax = plt.subplots(figsize=(9, 9))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    draw_land(ax, bounds)
    draw_cables(ax, bounds)

    cuts = quantile_cuts([h.get('loiter_hours') or 0 for h in hotspots],
                         len(HEAT_COLORS))
    draw_hotspot_cells(ax, hotspots, cuts)
    draw_vessel_points(ax, (report or {}).get('vessels'))

    label = report.get('week') or report.get('month') or ''
    period = f"{report.get('start', '')} ~ {report.get('end', '')}"
    total_h = (report.get('summary') or {}).get('cable_loiter_hours_total')
    # 英文標題：runner 缺 CJK 字型時才不會整行變豆腐
    ax.set_title(
        f"Loitering Hotspots {label}  ({period} UTC)"
        + (f"  ·  {total_h:g} h total" if total_h is not None else ''),
        color=TEXT, fontsize=11, pad=12)
    if title:
        ax.set_title(title, color=TEXT, fontsize=11, pad=12)

    handles = [Patch(facecolor=c, edgecolor=c, alpha=HOTSPOT_ALPHA, label=lb)
               for c, lb in legend_bins(hotspots, cuts)]
    if handles:
        ax.legend(handles=handles, loc='lower right', fontsize=8,
                  title='Loiter hours / cell', title_fontsize=8,
                  facecolor=PANEL_BG, edgecolor=EDGE, labelcolor=MUTED)

    top = hotspots[0]
    ax.text(0.02, 0.98,
            f"Top cell {top['lat']:.1f}N {top['lon']:.1f}E\n"
            f"{top['loiter_hours']:g} h · {top['vessels']} vessels",
            transform=ax.transAxes, fontsize=9, color=TEXT,
            verticalalignment='top', zorder=5,
            bbox=dict(boxstyle='round,pad=0.5', facecolor=PANEL_BG,
                      alpha=0.9, edgecolor=EDGE))

    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.grid(True, color=GRID, linewidth=0.5, alpha=0.5)
    ax.set_aspect('equal')
    _style_axes(ax, 'Longitude', 'Latitude')

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    fig.savefig(output_path, dpi=200, bbox_inches='tight', facecolor=BG)
    plt.close(fig)
    return output_path


def render_breakdown_chart(report, output_path):
    """船種／船籍分布橫條圖。兩者皆空時回傳 None。"""
    summary = (report or {}).get('summary') or {}
    types = top_types(summary)
    flags = top_flags(summary)
    if not types and not flags:
        return None

    plt = _setup_matplotlib()
    fig, axes = plt.subplots(1, 2, figsize=(11, 5))
    fig.patch.set_facecolor(BG)

    label = report.get('week') or report.get('month') or ''
    fig.suptitle(f"High-Risk Vessel Breakdown {label}", color=TEXT, fontsize=12)

    ax = axes[0]
    if types:
        names = [t for t, _ in types][::-1]
        counts = [c for _, c in types][::-1]
        ax.barh(names, counts,
                color=[TYPE_COLORS.get(n, '#888') for n in names], alpha=0.85)
        for i, c in enumerate(counts):
            ax.text(c, i, f' {c}', va='center', color=TEXT, fontsize=8)
        ax.margins(x=0.12)   # 留白給條末的數值標籤，否則最大值會被裁到
    ax.set_title('By vessel type', color=MUTED, fontsize=10)
    _style_axes(ax)

    ax = axes[1]
    if flags:
        names = [f'{zh} ({mid})' for mid, zh, _ in flags][::-1]
        counts = [c for _, _, c in flags][::-1]
        ax.barh(names, counts, color='#00f5ff', alpha=0.75)
        for i, c in enumerate(counts):
            ax.text(c, i, f' {c}', va='center', color=TEXT, fontsize=8)
        ax.margins(x=0.12)
    ax.set_title('By flag state (MID)', color=MUTED, fontsize=10)
    _style_axes(ax)

    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor=BG)
    plt.close(fig)
    return output_path
