#!/usr/bin/env python3
"""Render a 9:16 China Coast Guard AIS timeline story from Tier-1 history.

Visual language: large calendar typography, progressive routes, pulsing current
positions, a date scrubber, story holds, and restrained overview/close-up camera
moves. Public AIS only; this is not a real-time tactical picture.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
DEFAULT_INPUT = DOCS_DIR / "ais_track_history.json"
DEFAULT_OUTPUT = BASE_DIR / "artifacts" / "ccg-timeline.mp4"
TW_TZ = timezone(timedelta(hours=8))
MAP_BOUNDS = (20.7, 27.1, 117.0, 123.8)

# Broad storytelling anchors, not operational geofences.
FOCUS_ZONES = [
    ("KINMEN", 24.45, 118.35, 0.80),
    ("MATSU", 26.15, 119.95, 0.85),
    ("PENGHU", 23.55, 119.62, 0.95),
    ("TAIWAN STRAIT", 24.05, 119.55, 1.25),
    ("SW TAIWAN", 22.65, 120.20, 0.95),
]

BG = "#07111f"
GRID = "#203652"
TEXT = "#f7f9fd"
MUTED = "#8fa6c4"
ACCENT = "#4da3ff"
ACCENT_SOFT = "#91c7ff"
CCG = "#ffffff"
TRAIL = "#72b8ff"
OLD_TRAIL = "#42688f"

_COAST_GUARD_NAME = re.compile(
    r"(?:CHINA\s*COAST\s*GUARD|CHINACOASTGUARD|\bCCG\b|中國海警|海警)", re.I
)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TW_TZ)


def is_coast_guard(vessel: dict[str, Any]) -> bool:
    if vessel.get("gov") == "coastguard" or vessel.get("type_name") == "coastguard":
        return True
    return bool(_COAST_GUARD_NAME.search(str(vessel.get("name") or "")))


def short_name(vessel: dict[str, Any]) -> str:
    name = re.sub(r"\s+", " ", str(vessel.get("name") or "").strip())
    name = re.sub(r"CHINA\s*COAST\s*GUARD", "CCG", name, flags=re.I)
    if name:
        return name[:18]
    mmsi = str(vessel.get("mmsi") or "")
    return f"CCG {mmsi[-5:]}" if mmsi else "CCG"


def load_frames(path: Path, days: int = 14) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("AIS track history must be a list of snapshot entries")

    parsed: list[tuple[datetime, list[dict[str, Any]]]] = []
    for entry in raw:
        dt = parse_timestamp(entry.get("timestamp"))
        if dt is None:
            continue
        vessels = []
        for vessel in entry.get("vessels", []) or []:
            if not is_coast_guard(vessel):
                continue
            lat, lon = vessel.get("lat"), vessel.get("lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            if MAP_BOUNDS[0] <= lat <= MAP_BOUNDS[1] and MAP_BOUNDS[2] <= lon <= MAP_BOUNDS[3]:
                vessels.append(vessel)
        parsed.append((dt, vessels))

    if not parsed:
        return []
    parsed.sort(key=lambda item: item[0])
    cutoff = parsed[-1][0] - timedelta(days=max(days, 1))
    return [{"timestamp": dt, "vessels": vessels} for dt, vessels in parsed if dt >= cutoff]


def choose_frame_indices(frame_count: int, duration: float, fps: int) -> list[int]:
    if frame_count <= 0:
        return []
    target = max(2, int(round(max(duration, 1.0) * max(fps, 1))))
    if frame_count == 1:
        return [0] * target
    return [round(i * (frame_count - 1) / (target - 1)) for i in range(target)]


def nearest_story_zone(frame: dict[str, Any]) -> tuple[str | None, float | None]:
    best: tuple[str | None, float | None] = (None, None)
    for vessel in frame.get("vessels", []) or []:
        lat, lon = float(vessel["lat"]), float(vessel["lon"])
        for name, zlat, zlon, radius in FOCUS_ZONES:
            dx = (lon - zlon) * math.cos(math.radians((lat + zlat) / 2))
            dist = math.hypot(dx, lat - zlat)
            if dist <= radius and (best[1] is None or dist < best[1]):
                best = (name, dist)
    return best


def frame_interest_score(frame: dict[str, Any]) -> float:
    vessels = frame.get("vessels", []) or []
    unique = {str(v.get("mmsi") or short_name(v)) for v in vessels}
    score = min(3.0, 0.55 * len(unique))
    if nearest_story_zone(frame)[0]:
        score += 2.0
    return score


def choose_story_frame_indices(frames: list[dict[str, Any]], duration: float, fps: int) -> list[int]:
    if not frames:
        return []
    target = max(2, int(round(max(duration, 1.0) * max(fps, 1))))
    if len(frames) == 1:
        return [0] * target
    weights = [1.0 + min(2.4, frame_interest_score(f) * 0.38) for f in frames]
    cumulative, total = [], 0.0
    for weight in weights:
        total += weight
        cumulative.append(total)
    indices = [
        min(bisect_left(cumulative, (i / (target - 1)) * total), len(frames) - 1)
        for i in range(target)
    ]
    indices[0], indices[-1] = 0, len(frames) - 1
    return indices


def build_history(frames: Iterable[dict[str, Any]]) -> dict[str, list[tuple[datetime, float, float]]]:
    history: dict[str, list[tuple[datetime, float, float]]] = defaultdict(list)
    for frame in frames:
        for vessel in frame["vessels"]:
            key = str(vessel.get("mmsi") or short_name(vessel))
            history[key].append((frame["timestamp"], float(vessel["lat"]), float(vessel["lon"])))
    return history


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def camera_bounds_for_frame(frame: dict[str, Any]) -> tuple[float, float, float, float]:
    """Return a broad close-up that still contains every active CCG vessel."""
    vessels = frame.get("vessels", []) or []
    zone, _ = nearest_story_zone(frame)
    if not vessels or (zone is None and len(vessels) < 3):
        return MAP_BOUNDS

    lats = [float(v["lat"]) for v in vessels]
    lons = [float(v["lon"]) for v in vessels]
    lat_center = (min(lats) + max(lats)) / 2
    lon_center = (min(lons) + max(lons)) / 2

    overview_lat_span = MAP_BOUNDS[1] - MAP_BOUNDS[0]
    overview_lon_span = MAP_BOUNDS[3] - MAP_BOUNDS[2]
    min_lat_span = 4.8 if zone else 5.5
    min_lon_span = 5.1 if zone else 5.8
    # Add a full degree of breathing room so current markers/labels do not clip.
    lat_span = min(overview_lat_span, max(min_lat_span, max(lats) - min(lats) + 1.0))
    lon_span = min(overview_lon_span, max(min_lon_span, max(lons) - min(lons) + 1.0))
    lat_center = _clamp(lat_center, MAP_BOUNDS[0] + lat_span / 2, MAP_BOUNDS[1] - lat_span / 2)
    lon_center = _clamp(lon_center, MAP_BOUNDS[2] + lon_span / 2, MAP_BOUNDS[3] - lon_span / 2)
    return (
        lat_center - lat_span / 2,
        lat_center + lat_span / 2,
        lon_center - lon_span / 2,
        lon_center + lon_span / 2,
    )


def smooth_camera_bounds(
    frames: list[dict[str, Any]], indices: list[int], easing: float = 0.10
) -> list[tuple[float, float, float, float]]:
    if not indices:
        return []
    current = tuple(float(v) for v in MAP_BOUNDS)
    alpha = _clamp(easing, 0.02, 1.0)
    result = []
    for idx in indices:
        target = camera_bounds_for_frame(frames[idx])
        current = tuple(c + (t - c) * alpha for c, t in zip(current, target))
        result.append(current)
    return result


def _configure_fonts(plt: Any) -> None:
    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK TC", "Noto Sans CJK SC", "WenQuanYi Zen Hei", "DejaVu Sans"
    ]
    plt.rcParams["axes.unicode_minus"] = False


def _draw_static_map(ax: Any) -> None:
    from map_basemap import draw_land

    ax.set_facecolor(BG)
    draw_land(ax, MAP_BOUNDS, zorder=1, linewidth=0.65)
    ax.set_xlim(MAP_BOUNDS[2], MAP_BOUNDS[3])
    ax.set_ylim(MAP_BOUNDS[0], MAP_BOUNDS[1])
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([118, 120, 122])
    ax.set_yticks([22, 24, 26])
    ax.grid(True, color=GRID, alpha=0.42, linewidth=0.7)
    ax.tick_params(colors=MUTED, labelsize=10.5, length=0)
    for spine in ax.spines.values():
        spine.set_color(GRID)
        spine.set_linewidth(0.9)
    for lat, lon, label in [
        (23.72, 120.95, "TAIWAN"), (24.45, 118.35, "KINMEN"),
        (26.15, 119.95, "MATSU"), (23.55, 119.62, "PENGHU"),
        (24.10, 119.15, "TAIWAN STRAIT"),
    ]:
        ax.text(lon, lat, label, color=MUTED, fontsize=10.5, alpha=0.72,
                ha="center", va="center", zorder=2)


def render_video(
    frames: list[dict[str, Any]], output: Path, duration: float = 20.0,
    fps: int = 30, trail_hours: float = 72.0, dpi: int = 100,
    preview: Path | None = None,
) -> Path:
    if not frames:
        raise RuntimeError("No AIS snapshots available for the requested period")
    if not any(frame["vessels"] for frame in frames):
        raise RuntimeError("No China Coast Guard AIS positions found in the requested period")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter
    from matplotlib.patheffects import withStroke

    _configure_fonts(plt)
    output.parent.mkdir(parents=True, exist_ok=True)
    if preview:
        preview.parent.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(10.8, 19.2), dpi=dpi, facecolor=BG)
    ax = fig.add_axes([0.055, 0.185, 0.89, 0.60])
    _draw_static_map(ax)

    fig.text(0.07, 0.958, "TIMELINE", color=ACCENT, fontsize=12, fontweight="bold", va="top")
    month_text = fig.text(0.07, 0.927, "", color=TEXT, fontsize=34, fontweight="bold", va="top")
    day_text = fig.text(0.07, 0.885, "", color=TEXT, fontsize=52, fontweight="bold", va="top")
    year_text = fig.text(0.245, 0.894, "", color=MUTED, fontsize=16, fontweight="bold", va="top")
    time_text = fig.text(0.245, 0.868, "", color=MUTED, fontsize=14, va="top")
    fig.text(0.93, 0.958, "CHINA COAST GUARD", color=MUTED, fontsize=11,
             fontweight="bold", ha="right", va="top")
    count_text = fig.text(0.93, 0.914, "", color=TEXT, fontsize=38,
                          fontweight="bold", ha="right", va="top")
    fig.text(0.93, 0.879, "VESSELS OBSERVED", color=MUTED, fontsize=10.5,
             ha="right", va="top")

    prev_day = fig.text(0.12, 0.812, "", color=MUTED, fontsize=12, ha="center", va="center")
    current_day = fig.text(0.50, 0.812, "", color=TEXT, fontsize=15,
                           fontweight="bold", ha="center", va="center")
    next_day = fig.text(0.88, 0.812, "", color=MUTED, fontsize=12, ha="center", va="center")
    focus_text = fig.text(0.50, 0.792, "", color=ACCENT_SOFT, fontsize=10.5,
                          fontweight="bold", ha="center", va="center")

    line_bg = plt.Line2D([0.07, 0.93], [0.105, 0.105], transform=fig.transFigure,
                         color=GRID, linewidth=7, solid_capstyle="round")
    line_fg = plt.Line2D([0.07, 0.07], [0.105, 0.105], transform=fig.transFigure,
                         color=ACCENT, linewidth=7, solid_capstyle="round")
    thumb = plt.Line2D([0.07], [0.105], transform=fig.transFigure, color=CCG,
                       marker="o", markersize=9, markeredgecolor=ACCENT,
                       markeredgewidth=2, linestyle="None")
    fig.lines.extend([line_bg, line_fg, thumb])
    progress_text = fig.text(0.07, 0.078, "", color=MUTED, fontsize=11.5, va="top")
    fig.text(0.07, 0.047,
             "Public AIS observations. Signals may be absent, delayed, spoofed, or incomplete; not a real-time tactical picture.",
             color=MUTED, fontsize=9.8, va="top", wrap=True)
    fig.text(0.93, 0.022, "Taiwan Gray Zone Monitor", color=MUTED,
             fontsize=10.5, ha="right", va="bottom")

    indices = choose_story_frame_indices(frames, duration, fps)
    cameras = smooth_camera_bounds(frames, indices)
    history = build_history(frames)
    source_start, source_end = frames[0]["timestamp"], frames[-1]["timestamp"]
    trail_delta = timedelta(hours=max(trail_hours, 1.0))
    artists: list[Any] = []
    stroke = [withStroke(linewidth=3.6, foreground=BG)]

    def clear_dynamic() -> None:
        while artists:
            artist = artists.pop()
            try:
                artist.remove()
            except (ValueError, AttributeError):
                pass

    def draw_frame(source_idx: int, video_idx: int) -> None:
        clear_dynamic()
        frame, now = frames[source_idx], frames[source_idx]["timestamp"]
        vessels = frame["vessels"]
        lat_min, lat_max, lon_min, lon_max = cameras[video_idx]
        ax.set_xlim(lon_min, lon_max)
        ax.set_ylim(lat_min, lat_max)

        visible_ids: set[str] = set()
        for vessel in vessels:
            key = str(vessel.get("mmsi") or short_name(vessel))
            visible_ids.add(key)
            pts_all = [p for p in history.get(key, []) if p[0] <= now]
            pts_recent = [p for p in pts_all if now - trail_delta <= p[0]]
            if len(pts_all) >= 2:
                artists.append(ax.plot([p[2] for p in pts_all], [p[1] for p in pts_all],
                                       color=OLD_TRAIL, linewidth=1.25, alpha=0.26,
                                       zorder=3, solid_capstyle="round")[0])
            if len(pts_recent) >= 2:
                artists.append(ax.plot([p[2] for p in pts_recent], [p[1] for p in pts_recent],
                                       color=TRAIL, linewidth=2.4, alpha=0.78,
                                       zorder=4, solid_capstyle="round")[0])

        if vessels:
            lons = [float(v["lon"]) for v in vessels]
            lats = [float(v["lat"]) for v in vessels]
            pulse = 0.5 + 0.5 * math.sin(video_idx * 2 * math.pi / max(8, fps))
            artists.append(ax.scatter(lons, lats, s=360 + 180 * pulse, color=ACCENT,
                                      alpha=0.07, edgecolors="none", zorder=5))
            artists.append(ax.scatter(lons, lats, s=180 + 90 * pulse, color=ACCENT,
                                      alpha=0.14, edgecolors="none", zorder=5))
            artists.append(ax.scatter(lons, lats, s=68, color=CCG, edgecolors=ACCENT,
                                      linewidths=1.7, zorder=6))
            for vessel in vessels[:5]:
                txt = ax.annotate(short_name(vessel),
                                  (float(vessel["lon"]), float(vessel["lat"])),
                                  xytext=(7, 7), textcoords="offset points",
                                  color=TEXT, fontsize=9.0, fontweight="bold", zorder=7)
                txt.set_path_effects(stroke)
                artists.append(txt)

        month_text.set_text(now.strftime("%b").upper())
        day_text.set_text(now.strftime("%d"))
        year_text.set_text(now.strftime("%Y"))
        time_text.set_text(now.strftime("%H:%M  UTC+8"))
        count_text.set_text(str(len(visible_ids)))
        prev_day.set_text((now - timedelta(days=1)).strftime("%b %d").upper())
        current_day.set_text(now.strftime("%A · %b %d").upper())
        next_day.set_text((now + timedelta(days=1)).strftime("%b %d").upper())
        zone, _ = nearest_story_zone(frame)
        focus_text.set_text(f"●  FOCUS · {zone}" if zone else "OVERVIEW · TAIWAN WATERS")

        elapsed_days = max(0, (now.date() - source_start.date()).days)
        total_days = max(1, (source_end.date() - source_start.date()).days + 1)
        progress_text.set_text(
            f"DAY {min(elapsed_days + 1, total_days)} / {total_days}     {source_start:%Y-%m-%d}  →  {source_end:%Y-%m-%d}"
        )
        progress = video_idx / max(1, len(indices) - 1)
        x = 0.07 + 0.86 * progress
        line_fg.set_xdata([0.07, x])
        thumb.set_xdata([x])

    if preview:
        draw_frame(indices[-1], len(indices) - 1)
        fig.savefig(preview, dpi=dpi, facecolor=BG)

    writer = FFMpegWriter(
        fps=max(1, fps), codec="libx264", bitrate=6500,
        metadata={
            "title": "China Coast Guard AIS Timeline Around Taiwan",
            "artist": "Taiwan Gray Zone Monitor",
            "comment": "Public AIS observations; not a real-time tactical picture.",
        },
        extra_args=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
    )
    with writer.saving(fig, str(output), dpi=dpi):
        for video_idx, source_idx in enumerate(indices):
            draw_frame(source_idx, video_idx)
            writer.grab_frame(facecolor=BG)
    plt.close(fig)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a portrait CCG AIS timeline MP4")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--preview", type=Path, default=None)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--trail-hours", type=float, default=72.0)
    parser.add_argument("--dpi", type=int, default=100, help="100 dpi = 1080x1920")
    args = parser.parse_args()

    if not args.input.exists():
        parser.error(f"input file not found: {args.input}")
    frames = load_frames(args.input, days=args.days)
    ccg_positions = sum(len(f["vessels"]) for f in frames)
    unique = {str(v.get("mmsi") or short_name(v)) for f in frames for v in f["vessels"]}
    if not unique:
        raise SystemExit("No China Coast Guard AIS positions found; refusing to render an empty/misleading video")
    print(f"Loaded {len(frames)} snapshots, {ccg_positions} CCG positions, {len(unique)} unique vessels")
    out = render_video(frames, args.output, args.duration, args.fps,
                       args.trail_hours, args.dpi, args.preview)
    print(f"✅ CCG timeline video: {out}")
    if args.preview:
        print(f"✅ Preview image: {args.preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
