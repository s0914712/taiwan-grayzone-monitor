#!/usr/bin/env python3
"""
================================================================================
個案航跡圖 — 目標船 × 伴隨船 × 關機區間 × SAR 暗船點
================================================================================

畫 `src/archive_case.py` 產出的個案檔。與公務船航跡圖的差別在於它要同時表達
四件事，而不只是「船去過哪裡」：

  1. **漂 vs 走** —— 同一條航跡上，低速漂流段與正常航行段用不同顏色。
     影子船隊的行為特徵不在去了哪，而在哪一段停下來。
  2. **關機區間** —— AIS 中斷的兩端以虛線相連並標出時數與期間平均位移速度。
     關機趕路（數節）與關機後原地漂（<2 節）是完全不同的事。
  3. **伴隨船** —— 曾經接近的船，在最近接觸點標船名與距離。
  4. **SAR 暗船點** —— 雷達看得到、AIS 上不存在的目標；預設只畫落在目標船
     關機期間的那些（其餘是同期的一般暗船，畫上去只是雜訊）。

用法：
    python3 src/plot_encounter.py data/cases/medna_620999315_2026-08.json \\
        -o reports/medna_620999315_2026-08.png

繪圖層（matplotlib）在函式內 import，純函式可直接單元測試。
================================================================================
"""
import argparse
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from detect_ship_transfers import is_drifting_gap  # noqa: E402
from map_basemap import draw_cables, draw_land  # noqa: E402

# 低於此速度視為「漂流」——與 detect_ship_transfers.RENDEZVOUS_MAX_SPEED_KN 同值，
# 圖上看到的黃色段就是會合偵測會納入判定的那些點
DRIFT_SPEED_KN = 3.0
# SAR 暗船點離關機海域多遠仍算相關（公里）
MARKER_MAX_KM = 60.0

BG = '#0a1628'
TARGET_COLOR = '#ff2d55'      # --sev-critical
DRIFT_COLOR = '#ffd700'       # --accent-yellow：漂流段
MARKER_COLOR = '#00ff88'      # SAR 暗船點（可能就是目標船）
# 判讀結果的配色與說明。`not_target` 已被過境時刻排除，畫得比較暗 ——
# 它仍然是一艘沒在播報 AIS 的船，只是不是這艘。
VERDICT_COLOR = {"could_be_target": MARKER_COLOR,
                 "not_target": '#6b86b0',
                 "unknown": '#8aa4c8'}
VERDICT_LABEL = {"could_be_target": "could be target",
                 "not_target": "not this ship",
                 "unknown": "no pass time"}
# 伴隨船配色（避開海纜的青色與上面三色）
COMPANION_COLORS = ['#4d9fff', '#ff6b35', '#9b59b6', '#e8eef7',
                    '#00c2a8', '#ff8fb1', '#8ab4ff', '#b5c7e0']
# 標籤的六個擺放方向（逐艘輪替，避免擠在同一片海域的船名疊在一起）
LABEL_OFFSETS = [(20, 12), (20, -18), (-20, 12), (-20, -18), (0, 22), (0, -26)]


def split_by_speed(track, threshold=DRIFT_SPEED_KN):
    """把航跡切成 [(kind, points), …]，kind ∈ {'drift', 'transit'}。

    相鄰段共用邊界點，線才不會斷開。速度缺值當作航行（不硬指為漂流）。
    """
    if not track:
        return []
    segments = []
    current = []
    kind = None
    for p in track:
        speed = p.get("speed")
        k = "drift" if (speed is not None and speed < threshold) else "transit"
        if kind is None:
            kind = k
        elif k != kind:
            segments.append((kind, current + [p]))   # 邊界點兩段共用
            current = []
            kind = k
        current.append(p)
    if current:
        segments.append((kind, current))
    return segments


def case_bounds(case, pad=0.35, markers=(), frame="target"):
    """視野範圍 (lat_min, lat_max, lon_min, lon_max)。

    預設只用目標船航跡（＋要畫的 markers）定框：伴隨船的航跡是整個視窗的
    完整航程，多半縱貫整片監測海域，納入外框會把目標船那段壓扁到看不出形狀。
    伴隨船照畫，只是超出畫面的部分被裁掉 —— 這張圖要看的是接觸，不是它們
    各自去了哪。frame="all" 則把伴隨船一併納入。
    """
    lats = [p["lat"] for p in case["target"]["track"]]
    lons = [p["lon"] for p in case["target"]["track"]]
    if frame == "all":
        for c in case.get("companions", []):
            lats += [p["lat"] for p in c["track"]]
            lons += [p["lon"] for p in c["track"]]
    for m in markers:
        lats.append(m["lat"])
        lons.append(m["lon"])
    if not lats:
        raise ValueError("個案檔沒有任何座標")
    return (min(lats) - pad, max(lats) + pad, min(lons) - pad, max(lons) + pad)


def select_markers(case, all_markers=False, max_km=MARKER_MAX_KM):
    """要畫的 SAR 暗船點：關機期間、且在關機海域附近者。

    只用「同一天」篩會留下整片監測海域的暗船（實測 15 筆裡有 5 筆在台灣
    西岸，跟這艘船在巴士海峽關機毫無關係）；距離門檻把它收斂成真正可以拿去
    比對的那幾筆。
    """
    markers = case.get("markers", [])
    if all_markers:
        return markers
    return [m for m in markers
            if m.get("in_dark_gap")
            and (m.get("km_from_dark") is None or m["km_from_dark"] <= max_km)]


def drift_bounds(case, pad=0.12, threshold=DRIFT_SPEED_KN):
    """漂流段 + 關機端點的外框，供放大子圖使用。

    整段航程可能縱貫四個緯度，而值得細看的永遠是停下來的那一小塊 ——
    主圖保留全程脈絡，子圖放大這裡。沒有漂流段時回傳 None（不畫子圖）。
    """
    lats, lons = [], []
    for kind, pts in split_by_speed(case["target"]["track"], threshold):
        if kind != "drift":
            continue
        lats += [p["lat"] for p in pts]
        lons += [p["lon"] for p in pts]
    for g in case.get("dark_gaps", []):
        lats += [g["last_lat"], g["resume_lat"]]
        lons += [g["last_lon"], g["resume_lon"]]
    if not lats:
        return None
    return (min(lats) - pad, max(lats) + pad, min(lons) - pad, max(lons) + pad)


def marker_summary(marker):
    """SAR 偵測點旁的標註文字：判讀結果 + 被過境時刻排除了幾次。"""
    verdict = marker.get("verdict", "unknown")
    text = VERDICT_LABEL.get(verdict, verdict)
    checks = marker.get("pass_checks") or []
    ruled_out = [c for c in checks if not c["feasible"]]
    if checks and ruled_out:
        text += f"\n{len(ruled_out)}/{len(checks)} passes ruled out"
    return text


def info_lines(case, markers):
    """左上資訊框內容（英文 —— 沒有 CJK 字型的 runner 才不會整行變豆腐）。"""
    t = case["target"]
    w = case["window"]
    lines = [f"{t['name'] or t['mmsi']}  (MMSI {t['mmsi']}, {t['type_name']})",
             f"Track: {w['from']} → {w['to']}   {len(t['track'])} AIS fixes"]
    for g in case.get("dark_gaps", []):
        lines.append(f"AIS blackout {g['start'][5:16]} → {g['end'][5:16]}  "
                     f"{g['gap_hours']}h, {g['distance_km']}km "
                     f"= {g['drift_kn']}kn avg")
    if markers:
        counts = {}
        for m in markers:
            v = m.get("verdict", "unknown")
            counts[v] = counts.get(v, 0) + 1
        detail = ", ".join(f"{VERDICT_LABEL.get(v, v)}: {n}"
                           for v, n in sorted(counts.items()))
        lines.append(f"SAR dark detections during blackout: {len(markers)}"
                     f"  ({detail})")
    return lines


def render_case(case, output_path, all_markers=False, pad=0.35, frame="target"):
    """畫出個案航跡圖，回傳輸出路徑。"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    from matplotlib.lines import Line2D

    plt.rcParams['font.sans-serif'] = [
        'WenQuanYi Zen Hei', 'Noto Sans CJK TC', 'Noto Sans CJK SC', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    markers = select_markers(case, all_markers)
    bounds = case_bounds(case, pad, markers, frame)
    lat_min, lat_max, lon_min, lon_max = bounds

    fig, ax = plt.subplots(figsize=(10, 10))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    draw_land(ax, bounds)
    draw_cables(ax, bounds)

    try:                                   # 地名僅供參照，取不到就不畫
        from plot_gov_vessel_tracks import LANDMARKS
    except Exception:
        LANDMARKS = []
    for la, lo, label in LANDMARKS:
        if lat_min <= la <= lat_max and lon_min <= lo <= lon_max:
            ax.text(lo, la, label, fontsize=8, color='#6b86b0', style='italic',
                    ha='center', va='center', zorder=2, alpha=0.85)

    stroke = [pe.withStroke(linewidth=2.2, foreground=BG)]
    track = case["target"]["track"]

    def draw_layers(ax, labels=True):
        """把所有圖層畫到 ax 上；labels=False 供放大子圖使用（只留圖形）。"""
        _draw_companions(ax, labels)
        _draw_target(ax)
        _draw_dark_gaps(ax, labels)
        _draw_endpoints(ax, labels)
        for m in markers:
            color = VERDICT_COLOR.get(m.get("verdict", "unknown"), MARKER_COLOR)
            ax.plot(m["lon"], m["lat"], 'x', color=color, markersize=8,
                    markeredgewidth=1.6, zorder=6)
            if labels:
                # 標到左上：偵測點通常落在關機區間的標註附近，往右會疊字
                ax.annotate(marker_summary(m), (m["lon"], m["lat"]),
                            textcoords='offset points', xytext=(-10, 6),
                            ha='right', fontsize=6, color=color, zorder=7,
                            path_effects=stroke)

    # ── 伴隨船 ──
    def _draw_companions(ax, labels=True):
      for i, c in enumerate(case.get("companions", [])):
        color = COMPANION_COLORS[i % len(COMPANION_COLORS)]
        ax.plot([p["lon"] for p in c["track"]], [p["lat"] for p in c["track"]],
                color=color, linewidth=1.0, alpha=0.65,
                marker='o', markersize=1.6, zorder=3)
        if not labels:
            continue
        # 標在最近接觸點上，而不是航跡終點 —— 這張圖要問的是「靠多近」
        anchor = next((p for p in c["track"] if p["t"] == c.get("closest_at")),
                      c["track"][-1])
        km = c.get("min_km")
        label = c["name"] or c["mmsi"]
        if km is not None:
            label += f"\n{km:g} km"
        # 伴隨船常常擠在同一片海域，標籤六向交錯並拉一條細引線 ——
        # 推得夠遠才不會疊字，有引線才知道哪個名字屬於哪條航跡
        dx, dy = LABEL_OFFSETS[i % len(LABEL_OFFSETS)]
        ax.annotate(label, (anchor["lon"], anchor["lat"]),
                    textcoords='offset points', xytext=(dx, dy),
                    ha='left' if dx > 0 else ('right' if dx < 0 else 'center'),
                    fontsize=6.5, color=color, zorder=6, path_effects=stroke,
                    arrowprops=dict(arrowstyle='-', color=color, lw=0.6,
                                    alpha=0.7, shrinkA=0, shrinkB=2))

    # ── 目標船：漂流段 / 航行段分色 ──
    def _draw_target(ax):
      for kind, pts in split_by_speed(track):
        color = DRIFT_COLOR if kind == "drift" else TARGET_COLOR
        ax.plot([p["lon"] for p in pts], [p["lat"] for p in pts],
                color=color, linewidth=2.4 if kind == "drift" else 1.8,
                marker='o', markersize=3.0, zorder=5, solid_capstyle='round')

    # ── 關機區間 ──
    # 「關機後在漂」與「關機趕路」畫成兩種樣式：一張圖上兩段同樣是 AIS 中斷，
    # 但只有 <2kn 那段符合海上過駁的樣子，不分開等於把結論交給讀者猜。
    def _draw_dark_gaps(ax, labels=True):
      for j, g in enumerate(case.get("dark_gaps", [])):
        drifting = is_drifting_gap(g)
        ax.plot([g["last_lon"], g["resume_lon"]], [g["last_lat"], g["resume_lat"]],
                color=TARGET_COLOR, linewidth=2.0 if drifting else 1.1,
                linestyle=':', alpha=0.95 if drifting else 0.45, zorder=5)
        # ▼＝訊號在此消失，▲＝在此重新出現（與 SAR 的 × 不會看錯）
        ax.plot(g["last_lon"], g["last_lat"], 'v', color=TARGET_COLOR,
                markersize=9, markeredgecolor='white', markeredgewidth=0.7, zorder=6)
        ax.plot(g["resume_lon"], g["resume_lat"], '^', color=TARGET_COLOR,
                markersize=9, markeredgecolor='white', markeredgewidth=0.7, zorder=6)
        if not labels:
            continue
        mid_lon = (g["last_lon"] + g["resume_lon"]) / 2
        mid_lat = (g["last_lat"] + g["resume_lat"]) / 2
        # 標籤保持短句 —— 完整時間與距離已經在左上資訊框，地圖上只需要指出
        # 「這一段是關機」以及它到底是在漂還是在走
        dx, dy = (14, 14) if j % 2 == 0 else (16, -24)
        ax.annotate(f"AIS OFF {g['gap_hours']:g}h\n"
                    + (f"drifting {g['drift_kn']}kn" if drifting else "under way"),
                    (mid_lon, mid_lat), textcoords='offset points', xytext=(dx, dy),
                    ha='left' if dx > 0 else 'right',
                    fontsize=7, color=TARGET_COLOR,
                    alpha=1.0 if drifting else 0.6,
                    zorder=7, path_effects=stroke,
                    arrowprops=dict(arrowstyle='-', color=TARGET_COLOR, lw=0.6,
                                    alpha=0.6, shrinkA=0, shrinkB=2))

    # ── 起點 / 終點 ──
    def _draw_endpoints(ax, labels=True):
        ax.plot(track[0]["lon"], track[0]["lat"], 'o', color=TARGET_COLOR,
                markersize=9, markeredgecolor='white', markeredgewidth=0.8, zorder=7)
        ax.plot(track[-1]["lon"], track[-1]["lat"], 's', color=TARGET_COLOR,
                markersize=9, markeredgecolor='white', markeredgewidth=0.8, zorder=7)
        if not labels:
            return
        ax.annotate(f"start {track[0]['t'][5:16]}", (track[0]["lon"], track[0]["lat"]),
                    textcoords='offset points', xytext=(7, 4), fontsize=6.5,
                    color=TARGET_COLOR, zorder=7, path_effects=stroke)
        ax.annotate(f"last {track[-1]['t'][5:16]}", (track[-1]["lon"], track[-1]["lat"]),
                    textcoords='offset points', xytext=(7, -10), fontsize=6.5,
                    color=TARGET_COLOR, zorder=7, path_effects=stroke)

    draw_layers(ax, labels=True)

    # ── 放大子圖：漂流／關機那一小塊 ──
    # 主圖要保留「從哪來、往哪去」的脈絡，所以框很大，事件本身反而擠成一團。
    zoom = drift_bounds(case)
    if zoom:
        z_lat_min, z_lat_max, z_lon_min, z_lon_max = zoom
        axz = ax.inset_axes([0.03, 0.30, 0.40, 0.30])
        axz.set_facecolor(BG)
        draw_land(axz, zoom)
        draw_cables(axz, zoom)
        draw_layers(axz, labels=False)
        axz.set_xlim(z_lon_min, z_lon_max)
        axz.set_ylim(z_lat_min, z_lat_max)
        axz.set_xticks([])
        axz.set_yticks([])
        axz.set_aspect('equal')
        for spine in axz.spines.values():
            spine.set_color('#8aa4c8')
        ax.indicate_inset_zoom(axz, edgecolor='#8aa4c8', alpha=0.6, lw=0.8)
        axz.set_title('drift / blackout zone', color='#8aa4c8', fontsize=7, pad=3)

    ax.text(0.02, 0.98, "\n".join(info_lines(case, markers)),
            transform=ax.transAxes, fontsize=8.5, color='#e8eef7',
            verticalalignment='top',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#141e32', alpha=0.92,
                      edgecolor='#2a3a5a'), zorder=8)

    handles = [
        Line2D([0], [0], color=TARGET_COLOR, lw=2, label='Target — under way'),
        Line2D([0], [0], color=DRIFT_COLOR, lw=2.4,
               label=f'Target — drifting (<{DRIFT_SPEED_KN:g} kn)'),
        Line2D([0], [0], color=TARGET_COLOR, lw=2.0, ls=':',
               label='AIS blackout (▼ off  ▲ back on)'),
        Line2D([0], [0], color='#8aa4c8', lw=1, marker='o', markersize=3,
               label='Vessels that came close'),
    ]
    for verdict in sorted({m.get("verdict", "unknown") for m in markers}):
        handles.append(Line2D(
            [0], [0], color=VERDICT_COLOR.get(verdict, MARKER_COLOR), lw=0,
            marker='x', markersize=8,
            label=f"SAR dark in blackout — {VERDICT_LABEL.get(verdict, verdict)}"))
    handles.append(Line2D([0], [0], color='#00f5ff', lw=1, ls='--', alpha=0.5,
                          label='Submarine cables'))
    # 圖例放在圖框外下方：航跡本來就會延伸到四個角落，放在框內一定會壓到船名
    ax.legend(handles=handles, loc='upper left', bbox_to_anchor=(0, -0.07),
              ncol=3, fontsize=7.5, facecolor='#141e32', edgecolor='#2a3a5a',
              labelcolor='#8aa4c8')

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
    fig.savefig(output_path, dpi=200, bbox_inches='tight', facecolor=BG)
    plt.close(fig)
    print(f"✅ 個案航跡圖已輸出: {output_path}")
    return output_path


def main():
    ap = argparse.ArgumentParser(description="畫 archive_case.py 產出的個案航跡圖")
    ap.add_argument("case", help="個案 JSON（src/archive_case.py 的輸出）")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--all-markers", action="store_true",
                    help="畫出視窗內所有 SAR 暗船點，而非只畫關機期間的")
    ap.add_argument("--pad", type=float, default=0.35, help="視野留白（度）")
    ap.add_argument("--frame", choices=("target", "all"), default="target",
                    help="視野依目標船航跡（預設）或含伴隨船全程")
    args = ap.parse_args()

    with open(args.case, encoding="utf-8") as f:
        case = json.load(f)
    render_case(case, args.out, args.all_markers, args.pad, args.frame)


if __name__ == "__main__":
    main()
